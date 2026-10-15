"""Turn a Strava activity into stored runs / strength sessions."""
import json
import logging

from . import hevy, stats, strava

log = logging.getLogger(__name__)

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
STRENGTH_TYPES = {"WeightTraining", "Workout", "Crossfit"}


def runner_zones(runner) -> list[float]:
    if runner["hr_zones"]:
        return json.loads(runner["hr_zones"])
    return stats.default_zones(runner["max_hr"] or 190)


def store_run(conn, runner, act: dict, streams: dict) -> dict:
    zones = runner_zones(runner)
    t, hr, v = streams.get("time", []), streams.get("heartrate", []), streams.get("velocity_smooth", [])
    zone_s = stats.time_in_zones(t, hr, zones) if hr else [0] * 5
    dist_km = (act.get("distance") or 0) / 1000
    moving = act.get("moving_time") or 0
    run_type = stats.classify_run(act.get("name", ""), act.get("workout_type"), dist_km, zone_s)
    row = {
        "runner_id": runner["runner_id"],
        "strava_id": act["id"],
        "date": act["start_date_local"][:10],
        "name": act.get("name"),
        "distance_km": round(dist_km, 2),
        "duration_s": moving,
        "avg_pace_s_km": round(moving / dist_km, 1) if dist_km else None,
        "avg_hr": act.get("average_heartrate"),
        "max_hr": act.get("max_heartrate"),
        **{f"z{i + 1}_s": zone_s[i] for i in range(5)},
        "elevation_m": act.get("total_elevation_gain"),
        "temp_c": act.get("average_temp"),
        "hr_drift_pct": stats.hr_drift_pct(t, hr, v) if hr and v else None,
        "run_type": run_type,
    }
    row["is_easy_valid"] = int(bool(row["avg_hr"]) and stats.is_easy_valid(
        run_type, zone_s, row["elevation_m"], dist_km, row["temp_c"]))
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(row))})"
        f" ON CONFLICT(strava_id) DO UPDATE SET "
        + ", ".join(f"{c}=excluded.{c}" for c in row if c != "strava_id"),
        list(row.values()),
    )
    conn.commit()
    return row


def store_strength(conn, runner, act: dict) -> dict:
    desc = act.get("description") or ""
    sets, ok = hevy.parse_description(desc)
    rid = runner["runner_id"]
    conn.execute(
        "INSERT INTO strength_sessions (runner_id, strava_id, date, title, duration_s, avg_hr,"
        " raw_description, parse_ok) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(strava_id) DO UPDATE SET title=excluded.title, raw_description=excluded.raw_description,"
        " parse_ok=excluded.parse_ok, duration_s=excluded.duration_s, avg_hr=excluded.avg_hr",
        (rid, act["id"], act["start_date_local"][:10], act.get("name"), act.get("moving_time"),
         act.get("average_heartrate"), desc, int(ok)),
    )
    sid = conn.execute("SELECT id FROM strength_sessions WHERE strava_id=?", (act["id"],)).fetchone()["id"]
    conn.execute("DELETE FROM strength_sets WHERE session_id=?", (sid,))
    conn.executemany(
        "INSERT INTO strength_sets (session_id, runner_id, exercise, set_no, reps, weight_kg, duration_s,"
        " distance_m) VALUES (?,?,?,?,?,?,?,?)",
        [(sid, rid, s.exercise, s.set_no, s.reps, s.weight_kg, s.duration_s, s.distance_m) for s in sets],
    )
    conn.commit()
    if desc and not ok:
        log.warning("Hevy parse incomplete for activity %s (runner %s)", act["id"], rid)
    return {"session_id": sid, "sets": len(sets), "parse_ok": ok}


def process_activity(conn, runner, activity_id: int) -> tuple[str, dict] | None:
    """Fetch and store one activity. Returns (kind, stored_row) or None if ignored."""
    token = strava.access_token(conn, runner["runner_id"])
    act = strava.get_activity(token, activity_id)
    sport = act.get("sport_type") or act.get("type")
    if sport in RUN_TYPES:
        streams = strava.get_streams(token, activity_id)
        return "run", store_run(conn, runner, act, streams)
    if sport in STRENGTH_TYPES:
        return "strength", store_strength(conn, runner, act)
    return None
