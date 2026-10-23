"""Store uploaded activities (runs and strength sessions) for one runner."""
import hashlib
import json
import logging

from . import plans, stats, workout_text
from .fitfile import FitActivity

log = logging.getLogger(__name__)

# Runs shorter than both of these get stored but no individual debrief.
SHORT_RUN_KM = 3.0
SHORT_RUN_S = 20 * 60


def runner_zones(runner) -> list[float]:
    if runner["hr_zones"]:
        return json.loads(runner["hr_zones"])
    return stats.default_zones(runner["max_hr"] or 190)


def is_short(distance_km: float, duration_s: float) -> bool:
    return distance_km < SHORT_RUN_KM and duration_s < SHORT_RUN_S


def exists(conn, key: str) -> str | None:
    """Return 'run' / 'strength' if this activity is already stored."""
    if conn.execute("SELECT 1 FROM runs WHERE activity_key=?", (key,)).fetchone():
        return "run"
    if conn.execute("SELECT 1 FROM strength_sessions WHERE activity_key=?", (key,)).fetchone():
        return "strength"
    return None


def update_max_hr(conn, runner, observed: float | None) -> bool:
    """Raise the runner's max HR (and default zones) when a file shows a higher one.

    Zones set by Coach Andy (zones_locked=1) are never changed. Returns True if updated.
    """
    if not observed or observed < 120 or observed > 225:
        return False
    current = runner["max_hr"] or 0
    if observed <= current:
        return False
    locked = runner["zones_locked"] if "zones_locked" in runner.keys() else 0
    if locked:
        conn.execute("UPDATE runners SET max_hr=? WHERE runner_id=?", (int(observed), runner["runner_id"]))
    else:
        conn.execute("UPDATE runners SET max_hr=?, hr_zones=? WHERE runner_id=?",
                     (int(observed), json.dumps(stats.default_zones(observed)), runner["runner_id"]))
    conn.commit()
    return True


def store_run(conn, runner, act: FitActivity, key: str, note: str = "") -> dict:
    zones = runner_zones(runner)
    t, hr, v = act.time_s, act.hr, act.speed
    zone_s = stats.time_in_zones(t, hr, zones) if hr else [0] * 5
    dist_km = act.distance_m / 1000
    moving = act.timer_s or act.elapsed_s
    # The caption is the only "name" a file has; it lets "race"/"intervals"/"long" classify.
    run_type = stats.classify_run(note or "", None, dist_km, zone_s)
    temp = act.temp_c
    row = {
        "runner_id": runner["runner_id"],
        "activity_key": key,
        "source": "fit",
        "date": act.local_date,
        "name": "Treadmill run" if act.treadmill else "Run",
        "note": (note or "")[:500] or None,
        "treadmill": int(act.treadmill),
        "distance_km": round(dist_km, 2),
        "duration_s": int(moving),
        "avg_pace_s_km": round(moving / dist_km, 1) if dist_km else None,
        "avg_hr": act.avg_hr,
        "max_hr": act.max_hr,
        **{f"z{i + 1}_s": zone_s[i] for i in range(5)},
        "elevation_m": act.ascent_m,
        "temp_c": temp,
        "hr_drift_pct": stats.hr_drift_pct(t, hr, v) if hr and v else None,
        "run_type": run_type,
    }
    # Treadmill pace is not comparable to outdoor pace, so it never feeds the fitness check.
    row["is_easy_valid"] = int(bool(row["avg_hr"]) and not act.treadmill and stats.is_easy_valid(
        run_type, zone_s, row["elevation_m"], dist_km, temp))
    replaced = _replace_sheet_row(conn, row)
    cols = ", ".join(row)
    run_id = conn.execute(f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(row))})",
                          list(row.values())).lastrowid
    splits = stats.km_splits(t, v, hr) if v else []
    conn.executemany(
        "INSERT INTO run_splits (run_id, runner_id, km, distance_m, duration_s, pace_s_km, avg_hr, max_hr)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(run_id, runner["runner_id"], s["km"], s["distance_m"], s["duration_s"], s["pace_s_km"],
          s["avg_hr"], s["max_hr"]) for s in splits])
    conn.commit()
    row["replaced_sheet_row"] = replaced
    row["plan"] = plans.match_run(conn, runner, row)
    return row


def _replace_sheet_row(conn, row: dict) -> bool:
    """A .fit file for a run already imported from a spreadsheet replaces the spreadsheet row.

    Same date and distance within 10%: the file has the detail (zones, drift), so it wins.
    The spreadsheet note is kept if the file has no caption.
    """
    for old in conn.execute("SELECT id, activity_key, distance_km, note FROM runs WHERE runner_id=?"
                            " AND source='sheet' AND date=?", (row["runner_id"], row["date"])).fetchall():
        if old["distance_km"] and abs(old["distance_km"] - row["distance_km"]) <= 0.1 * old["distance_km"]:
            conn.execute("UPDATE planned_workouts SET status='planned', completed_activity_key=NULL"
                         " WHERE completed_activity_key=?", (old["activity_key"],))
            conn.execute("DELETE FROM runs WHERE id=?", (old["id"],))
            row["note"] = row["note"] or old["note"]
            return True
    return False


def _store_sets(conn, runner, sid: int, sets) -> None:
    conn.executemany(
        "INSERT INTO strength_sets (session_id, runner_id, exercise, set_no, reps, weight_kg, duration_s)"
        " VALUES (?,?,?,?,?,?,?)",
        [(sid, runner["runner_id"], s.exercise, s.set_no, s.reps, s.weight_kg, s.duration_s) for s in sets],
    )


def store_strength_fit(conn, runner, act: FitActivity, key: str, note: str = "") -> dict:
    cur = conn.execute(
        "INSERT INTO strength_sessions (runner_id, activity_key, source, date, title, note, duration_s,"
        " avg_hr, parse_ok) VALUES (?,?,?,?,?,?,?,?,?)",
        (runner["runner_id"], key, "fit", act.local_date, "Strength session", (note or "")[:500] or None,
         int(act.timer_s or act.elapsed_s), act.avg_hr, int(bool(act.sets))),
    )
    _store_sets(conn, runner, cur.lastrowid, act.sets)
    conn.commit()
    plan = plans.match_strength(conn, runner["runner_id"], act.local_date, key)
    return {"session_id": cur.lastrowid, "sets": len(act.sets), "plan": plan}


def store_strength_text(conn, runner, text: str, date: str) -> dict:
    """A workout typed into the chat. Same text on the same day is stored once."""
    key = "text:" + hashlib.sha256(f"{runner['runner_id']}|{date}|{text.strip()}".encode()).hexdigest()
    if exists(conn, key):
        return {"duplicate": True}
    synced = conn.execute("SELECT id, title FROM strength_sessions WHERE runner_id=? AND date=? AND source='hevy'",
                          (runner["runner_id"], date)).fetchone()
    if synced:
        # The full workout is already here from Hevy; a typed copy would count it twice.
        return {"duplicate": True, "already_synced_from": "hevy", "session_id": synced["id"],
                "title": synced["title"]}
    sets, unparsed = workout_text.parse(text)
    cur = conn.execute(
        "INSERT INTO strength_sessions (runner_id, activity_key, source, date, title, raw_description,"
        " parse_ok) VALUES (?,?,?,?,?,?,?)",
        (runner["runner_id"], key, "text", date, "Strength session", text[:2000],
         int(bool(sets) and not unparsed)),
    )
    _store_sets(conn, runner, cur.lastrowid, sets)
    conn.commit()
    plan = plans.match_strength(conn, runner["runner_id"], date, key)
    return {"duplicate": False, "session_id": cur.lastrowid, "sets": len(sets), "unparsed": unparsed,
            "plan": plan}
