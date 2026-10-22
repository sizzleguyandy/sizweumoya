"""Match stored runs to Coach Andy's planned workouts and check them against the plan."""
import json
from datetime import date, timedelta

# A run counts for a planned session on the same day, or one day either side (runs often slip).
MATCH_DAYS = 1


def _target(w) -> dict:
    try:
        return json.loads(w["target"] or "{}")
    except ValueError:
        return {}


def match_run(conn, runner, run: dict) -> dict | None:
    """Link a just-stored run to the nearest open planned run session. Returns the comparison.

    Sets the run's type from the plan when the plan is explicit (benchmark, long, workout).
    """
    rid = runner["runner_id"]
    d = date.fromisoformat(run["date"])
    cands = conn.execute(
        "SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
        " WHERE w.runner_id=? AND p.active=1 AND w.status IN ('planned','missed')"
        " AND w.date BETWEEN ? AND ?",
        (rid, (d - timedelta(days=MATCH_DAYS)).isoformat(), (d + timedelta(days=MATCH_DAYS)).isoformat()),
    ).fetchall()
    cands = [w for w in cands if _target(w).get("kind") != "rest"]
    if not cands:
        return None
    km = run.get("distance_km") or 0
    # Nearest day first; on a tie the earlier session (runs slip late more often than early),
    # then the closest planned distance.
    w = min(cands, key=lambda w: (abs((date.fromisoformat(w["date"]) - d).days), w["date"] > run["date"],
                                  abs((_target(w).get("km") or 0) - km)))
    t = _target(w)
    conn.execute("UPDATE planned_workouts SET status='done', completed_activity_key=? WHERE id=?",
                 (run["activity_key"], w["id"]))
    kind = t.get("kind")
    # A cut-short long run (e.g. a first run back after illness) keeps its own type.
    short_long = kind == "long" and km < 0.6 * (t.get("km") or 0)
    if kind in ("benchmark", "long", "workout", "race") and run.get("run_type") != "race" and not short_long:
        conn.execute("UPDATE runs SET run_type=? WHERE activity_key=?", (kind, run["activity_key"]))
        run["run_type"] = kind
    conn.commit()
    return compare(runner, run, w["date"], w["description"], t)


def hr_cap_for(runner, target: dict) -> int | None:
    if target.get("hr_max"):
        return target["hr_max"]
    if target.get("kind") in ("easy", "long"):
        return runner["easy_hr_cap"]
    return None


def compare(runner, run: dict, planned_date: str, description: str, target: dict) -> dict:
    out = {"planned_date": planned_date, "description": description, "kind": target.get("kind"),
           "planned_km": target.get("km"), "actual_km": run.get("distance_km")}
    if target.get("km") and run.get("distance_km"):
        out["km_pct_of_plan"] = round(100 * run["distance_km"] / target["km"])
    if target.get("climb_m") and run.get("elevation_m") is not None:
        out["planned_climb_m"], out["actual_climb_m"] = target["climb_m"], run["elevation_m"]
    cap = hr_cap_for(runner, target)
    if cap:
        out["hr_cap"] = cap
        out["avg_hr"] = run.get("avg_hr")
        out["over_hr_cap"] = bool(run.get("avg_hr") and run["avg_hr"] > cap)
    if target.get("hr_range"):
        out["hr_range"] = target["hr_range"]
        if target.get("kind") == "long":
            out["includes_hm_effort"] = True  # part of the run is meant to be above the cap
    if planned_date != run["date"]:
        out["moved_days"] = (date.fromisoformat(run["date"]) - date.fromisoformat(planned_date)).days
    return out


def plan_for_run(conn, runner, activity_key: str, run: dict) -> dict | None:
    w = conn.execute("SELECT * FROM planned_workouts WHERE completed_activity_key=?", (activity_key,)).fetchone()
    return compare(runner, run, w["date"], w["description"], _target(w)) if w else None


def races(conn, rid: int, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for r in conn.execute("SELECT name, date, distance_km, ascent_m, note FROM races WHERE runner_id=?"
                          " AND date>=? ORDER BY date", (rid, today.isoformat())):
        r = dict(r)
        r["days_to_go"] = (date.fromisoformat(r["date"]) - today).days
        out.append(r)
    return out


# ---- coaching mode -------------------------------------------------------------
# Option 1 "monitor": no programme on file. Feedback, flags, taper suggestion, chat.
# Option 2 "guided": a run programme from Coach Andy is on file and still running.
#   Everything in option 1, plus Sarah talks the runner through the programme.

def _active(conn, rid: int, type_: str, today: date):
    return conn.execute(
        "SELECT * FROM plans WHERE runner_id=? AND type=? AND active=1"
        " AND (end_date IS NULL OR end_date>=?) ORDER BY start_date DESC LIMIT 1",
        (rid, type_, today.isoformat())).fetchone()


def mode(conn, rid: int, today: date | None = None) -> dict:
    """Which option applies, decided only by what is on file. No programme means option 1."""
    today = today or date.today()
    run = _active(conn, rid, "run", today)
    strength = _active(conn, rid, "strength", today)
    out = {"mode": "guided" if run else "monitor",
           "run_programme": None, "strength_programme": bool(strength)}
    if run:
        out["run_programme"] = {"start": run["start_date"], "end": run["end_date"],
                                "weeks_left": max(0, -(-(date.fromisoformat(run["end_date"]) - today).days // 7))
                                if run["end_date"] else None}
    return out


def sessions_around(conn, rid: int, today: date | None = None) -> dict:
    """Guided mode: the last session, today's, and the next few, from the active run programme."""
    today = today or date.today()
    m = mode(conn, rid, today)
    if m["mode"] != "guided":
        return {"mode": "monitor", "sessions": []}

    def pick(sql, args):
        out = []
        for w in conn.execute(sql, args).fetchall():
            t = _target(w)
            out.append({"date": w["date"], "description": w["description"], "status": w["status"],
                        **{k: t[k] for k in ("kind", "km", "hr_range", "climb_m", "week", "phase") if k in t}})
        return out

    base = ("SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
            " AND p.active=1 AND p.type='run' AND ")
    return {
        "mode": "guided",
        "last": pick(base + "w.date<? ORDER BY w.date DESC LIMIT 1", (rid, today.isoformat())),
        "today": pick(base + "w.date=?", (rid, today.isoformat())),
        "next": pick(base + "w.date>? ORDER BY w.date LIMIT 3", (rid, today.isoformat())),
    }


def tomorrow_session(conn, rid: int, today: date | None = None) -> list:
    today = today or date.today()
    s = sessions_around(conn, rid, today)
    tomorrow = (today + timedelta(days=1)).isoformat()
    return [w for w in s.get("next", []) if w["date"] == tomorrow]


def week_vs_programme(conn, rid: int, start: date, end: date, actual: dict) -> dict:
    """Guided mode: this week's programme against what was done."""
    ws = conn.execute(
        "SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND p.type='run' AND w.date>=? AND w.date<? ORDER BY w.date",
        (rid, start.isoformat(), end.isoformat())).fetchall()
    if not ws:
        return {"sessions_planned": 0}
    targets = [(w, _target(w)) for w in ws]
    planned_km = round(sum(t.get("km") or 0 for _, t in targets), 1)
    long_ = [(w, t) for w, t in targets if t.get("kind") in ("long", "race")]
    out = {
        "week": targets[0][1].get("week"), "phase": targets[0][1].get("phase"),
        "planned_km": planned_km, "actual_km": actual["total_km"],
        "km_pct_of_plan": round(100 * actual["total_km"] / planned_km) if planned_km else None,
        "sessions_planned": len(ws),
        "done": sum(1 for w in ws if w["status"] == "done"),
        "missed": [f"{w['date']} {w['description'][:40]}" for w in ws if w["status"] == "missed"],
        "still_to_come": [f"{w['date']} {w['description'][:40]}" for w in ws if w["status"] == "planned"],
    }
    if long_:
        w, t = long_[-1]
        got = conn.execute("SELECT distance_km, avg_hr, elevation_m FROM runs WHERE activity_key=?",
                           (w["completed_activity_key"],)).fetchone() if w["completed_activity_key"] else None
        out["long_run"] = {"planned_km": t.get("km"), "planned_climb_m": t.get("climb_m"),
                           "actual_km": got["distance_km"] if got else None,
                           "actual_climb_m": got["elevation_m"] if got else None,
                           "avg_hr": got["avg_hr"] if got else None}
    return out
