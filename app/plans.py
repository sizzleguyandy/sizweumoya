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
