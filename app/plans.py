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
    # A second run on a day that already has one matched is extra running, not tomorrow's
    # session done early: matching it forward would tick off (and take off the watch) a
    # session that is still to come.
    if conn.execute("SELECT 1 FROM planned_workouts w JOIN runs r ON r.activity_key=w.completed_activity_key"
                    " WHERE w.runner_id=? AND r.date=? AND r.activity_key!=?",
                    (rid, run["date"], run["activity_key"])).fetchone():
        cands = [w for w in cands if w["date"] <= run["date"]]
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


FOCUSES = ("running", "strength", "hybrid")


def today_info(today: date | None = None) -> dict:
    """Today's date as the server's clock has it, so the model never has to guess the day."""
    today = today or date.today()
    return {"date": today.isoformat(), "weekday": today.strftime("%A"),
            "tomorrow": (today + timedelta(days=1)).strftime("%A %Y-%m-%d")}


def mode(conn, rid: int, today: date | None = None) -> dict:
    """How Sarah works with this person, decided only by what is on file.

    focus: running, strength or hybrid (both). For each discipline: "guided" when a programme
    from Coach Andy is on file and still running, otherwise "monitor" (they log their own
    training and get feedback). `mode` is the running one, kept for older callers.
    """
    today = today or date.today()
    r = conn.execute("SELECT focus FROM runners WHERE runner_id=?", (rid,)).fetchone()
    focus = (r["focus"] if r and r["focus"] in FOCUSES else "running")
    run = _active(conn, rid, "run", today)
    strength = _active(conn, rid, "strength", today)

    def prog(p):
        if not p:
            return None
        return {"start": p["start_date"], "end": p["end_date"],
                "weeks_left": max(0, -(-(date.fromisoformat(p["end_date"]) - today).days // 7))
                if p["end_date"] else None}

    return {"focus": focus,
            "mode": "guided" if run else "monitor",
            "running": "guided" if run else "monitor",
            "strength": "guided" if strength else "monitor",
            "run_programme": prog(run), "strength_programme": prog(strength)}


def sessions_around(conn, rid: int, today: date | None = None) -> dict:
    """Programme sessions around today (last, today, next three) for each discipline that
    has a programme on file. Nothing for a discipline the person logs themselves."""
    today = today or date.today()
    m = mode(conn, rid, today)
    if m["running"] != "guided" and m["strength"] != "guided":
        return {"today_is": today_info(today), "mode": "monitor", "sessions": []}

    def pick(sql, args):
        out = []
        for w in conn.execute(sql, args).fetchall():
            t = _target(w)
            out.append({"date": w["date"], "weekday": date.fromisoformat(w["date"]).strftime("%A"),
                        "type": w["ptype"], "description": w["description"],
                        "status": w["status"],
                        **{k: t[k] for k in ("kind", "km", "hr_range", "climb_m", "week", "phase", "exercises")
                           if k in t}})
        return out

    base = ("SELECT w.*, p.type ptype FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
            " WHERE w.runner_id=? AND p.active=1 AND w.status!='cancelled' AND ")
    d = today.isoformat()
    return {
        "today_is": today_info(today),
        "mode": "guided", "running": m["running"], "strength": m["strength"],
        "last": pick(base + "w.date<? ORDER BY w.date DESC LIMIT 2", (rid, d)),
        "today": pick(base + "w.date=?", (rid, d)),
        "next": pick(base + "w.date>? ORDER BY w.date LIMIT 4", (rid, d)),
    }


def match_strength(conn, rid: int, session_date: str, activity_key: str) -> dict | None:
    """Link a logged strength session to the nearest open session of a strength programme."""
    d = date.fromisoformat(session_date)
    w = conn.execute(
        "SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND p.type='strength' AND w.status IN ('planned','missed') AND w.date BETWEEN ? AND ?"
        " ORDER BY abs(julianday(w.date)-julianday(?)), w.date LIMIT 1",
        (rid, (d - timedelta(days=MATCH_DAYS)).isoformat(), (d + timedelta(days=MATCH_DAYS)).isoformat(),
         session_date)).fetchone()
    if not w:
        return None
    conn.execute("UPDATE planned_workouts SET status='done', completed_activity_key=? WHERE id=?",
                 (activity_key, w["id"]))
    conn.commit()
    return {"planned_date": w["date"], "description": w["description"], **_target(w)}


def tomorrow_session(conn, rid: int, today: date | None = None) -> list:
    today = today or date.today()
    s = sessions_around(conn, rid, today)
    tomorrow = (today + timedelta(days=1)).isoformat()
    return [w for w in s.get("next", []) if w["date"] == tomorrow]


def strength_vs_programme(conn, rid: int, start: date, end: date) -> dict:
    """Strength programme sessions in the period: planned, done, missed, still to come."""
    ws = conn.execute(
        "SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND p.type='strength' AND w.date>=? AND w.date<? ORDER BY w.date",
        (rid, start.isoformat(), end.isoformat())).fetchall()
    return {"sessions_planned": len(ws), "done": sum(1 for w in ws if w["status"] == "done"),
            "missed": [f"{w['date']} {w['description'][:40]}" for w in ws if w["status"] == "missed"],
            "still_to_come": [f"{w['date']} {w['description'][:40]}" for w in ws if w["status"] == "planned"]}


def week_vs_programme(conn, rid: int, start: date, end: date, actual: dict) -> dict:
    """Guided mode: this week's run programme against what was done.

    When Coach Andy loaded a new programme partway through the week, the days before it
    started still count with the sessions of the programme it replaced, so the week is
    compared with everything that was planned for it, not just the second half."""
    ws = conn.execute(
        "SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND p.type='run' AND w.date>=? AND w.date<? AND w.status!='cancelled' ORDER BY w.date",
        (rid, start.isoformat(), end.isoformat())).fetchall()
    cur = conn.execute("SELECT id, start_date FROM plans WHERE runner_id=? AND type='run' AND active=1"
                       " ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    first = min([w["date"] for w in ws] + ([cur["start_date"]] if cur and cur["start_date"] else []), default=None)
    if cur and first and first > start.isoformat():
        prev = conn.execute("SELECT id FROM plans WHERE runner_id=? AND type='run' AND active=0 AND id<?"
                            " ORDER BY id DESC LIMIT 1", (rid, cur["id"])).fetchone()
        if prev:
            earlier = conn.execute(
                "SELECT * FROM planned_workouts WHERE plan_id=? AND date>=? AND date<? AND status!='cancelled'"
                " ORDER BY date",
                (prev["id"], start.isoformat(), first)).fetchall()
            ws = list(earlier) + list(ws)
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


# ---- changing the programme (only on Coach Andy's decision) ------------------------

class PlanChangeError(ValueError):
    pass


def move_session(conn, rid: int, from_date: str, to_date: str, note: str = "") -> dict:
    """Move one open session of the active programme to another day. Coach decisions only."""
    try:
        a, b = date.fromisoformat(from_date), date.fromisoformat(to_date)
    except ValueError:
        raise PlanChangeError("dates must be YYYY-MM-DD") from None
    ws = conn.execute(
        "SELECT w.* FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=? AND p.active=1"
        " AND w.date=? AND w.status IN ('planned','missed')", (rid, a.isoformat())).fetchall()
    if not ws:
        raise PlanChangeError(f"no open session on {a:%A} {a.isoformat()}")
    if len(ws) > 1:
        raise PlanChangeError(f"more than one session on {a.isoformat()}; say which one")
    w = ws[0]
    t = _target(w)
    t["day"] = b.strftime("%a")
    t.setdefault("moved_from", a.isoformat())
    if note:
        t["change_note"] = note[:200]
    conn.execute("UPDATE planned_workouts SET date=?, status='planned', target=? WHERE id=?",
                 (b.isoformat(), json.dumps(t), w["id"]))
    conn.commit()
    others = [x["description"] for x in conn.execute(
        "SELECT w.description FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND w.date=? AND w.id!=?", (rid, b.isoformat(), w["id"]))]
    return {"moved": w["description"], "from": f"{a:%A} {a.isoformat()}", "to": f"{b:%A} {b.isoformat()}",
            "also_on_that_day": others}
EDIT_KINDS = ("easy", "long", "workout", "benchmark", "race")


def edit_session(conn, rid: int, day: str, *, km: float | None = None, kind: str | None = None,
                 description: str | None = None, hr_low: int | None = None, hr_high: int | None = None,
                 rest: bool = False, note: str = "") -> dict:
    """Change one open session of the active programme: distance, kind, description, heart
    rate, or make it a rest day (cancelled; it leaves the calendar). Coach decisions only.
    Structured watch steps are dropped when the distance or kind changes (they would no
    longer match); easy runs get a fresh one-step workout on the next calendar update."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise PlanChangeError("date must be YYYY-MM-DD") from None
    ws = conn.execute(
        "SELECT w.*, p.type ptype FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND w.date=? AND w.status IN ('planned','missed')", (rid, d.isoformat())).fetchall()
    if not ws:
        raise PlanChangeError(f"no open session on {d:%A} {d.isoformat()}")
    if len(ws) > 1:
        runs = [w for w in ws if w["ptype"] == "run"]
        if len(runs) != 1:
            raise PlanChangeError(f"more than one session on {d.isoformat()}; change it in the programme file")
        ws = runs
    w = ws[0]
    t = _target(w)
    before = {"description": w["description"], "km": t.get("km"), "kind": t.get("kind"),
              "hr_range": t.get("hr_range"), "hr_max": t.get("hr_max")}
    if rest:
        conn.execute("UPDATE planned_workouts SET status='cancelled', target=? WHERE id=?",
                     (json.dumps({**t, "change_note": (note or "made a rest day")[:200]}), w["id"]))
        conn.commit()
        return {"date": f"{d:%A} {d.isoformat()}", "before": before, "after": "rest day (session removed)"}
    if kind is not None:
        if kind not in EDIT_KINDS:
            raise PlanChangeError(f"kind must be one of {', '.join(EDIT_KINDS)}")
        if kind != t.get("kind"):
            t.pop("steps", None)
        t["kind"] = kind
        if kind in ("easy", "long"):
            t.pop("hr_range", None)
    if km is not None:
        if not 0 < km <= 100:
            raise PlanChangeError("distance must be in km")
        if km != t.get("km"):
            t.pop("steps", None)
        t["km"] = round(float(km), 1)
    if hr_high:
        if hr_low:
            if not 80 <= hr_low < hr_high <= 230:
                raise PlanChangeError("heart rate range must be low-high, e.g. 152-162")
            t["hr_range"] = [int(hr_low), int(hr_high)]
            t.pop("hr_max", None)
        else:
            t["hr_max"] = int(hr_high)
            t.pop("hr_range", None)
    desc = (description or "").strip()[:300] or w["description"]
    t["change_note"] = (note or "changed by Coach Andy")[:200]
    conn.execute("UPDATE planned_workouts SET description=?, target=?, status='planned' WHERE id=?",
                 (desc, json.dumps(t), w["id"]))
    conn.commit()
    after = {"description": desc, "km": t.get("km"), "kind": t.get("kind"), "hr_range": t.get("hr_range"),
             "hr_max": t.get("hr_max")}
    return {"date": f"{d:%A} {d.isoformat()}", "before": before, "after": after}




def delete_run(conn, rid: int, run_id: int) -> dict:
    """Delete one stored run (e.g. half of a run stopped by mistake, once the joined file is in).
    A programme session it completed goes back to planned (or missed, if its day has passed),
    and the file is remembered so no sync brings it back."""
    r = conn.execute("SELECT id, date, distance_km, activity_key FROM runs WHERE id=? AND runner_id=?",
                     (run_id, rid)).fetchone()
    if r is None:
        raise PlanChangeError(f"runner {rid} has no run with id {run_id}")
    freed = [dict(w) for w in conn.execute("SELECT id, date, description FROM planned_workouts"
                                           " WHERE completed_activity_key=?", (r["activity_key"],))]
    for w in freed:
        conn.execute("UPDATE planned_workouts SET completed_activity_key=NULL, status=? WHERE id=?",
                     ("missed" if w["date"] < date.today().isoformat() else "planned", w["id"]))
    conn.execute("INSERT OR IGNORE INTO deleted_activities (activity_key, runner_id, date, distance_km)"
                 " VALUES (?,?,?,?)", (r["activity_key"], rid, r["date"], r["distance_km"]))
    conn.execute("DELETE FROM run_splits WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    conn.commit()
    return {"deleted": dict(r), "sessions_reopened": freed}


def replace_hr_cap(conn, rid: int, old: int, new: int) -> int:
    """Open easy and long sessions still carrying an old heart-rate cap get the new one (after
    Coach Andy changes the easy cap). Returns how many changed."""
    n = 0
    for w in conn.execute("SELECT w.id, w.target FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
                          " WHERE w.runner_id=? AND p.active=1 AND w.status IN ('planned','missed')", (rid,)).fetchall():
        t = _target(w)
        if t.get("kind") in ("easy", "long") and t.get("hr_max") == old:
            t["hr_max"] = new
            conn.execute("UPDATE planned_workouts SET target=? WHERE id=?", (json.dumps(t), w["id"]))
            n += 1
    conn.commit()
    return n
