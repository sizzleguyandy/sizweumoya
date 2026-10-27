"""Race-day pacing plan: predicted finish and goal pace from the runner's VDOT, the race
library's course guide and personal splits (for library races), and, once race day is inside
the 16-day forecast, the same numbers adjusted with TRUE PACE for the forecast weather.
"""
from datetime import date, datetime, timedelta

from . import intake, paces, racelib, stats, truepace

FORECAST_DAYS = 15


def _fmt_time(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _vdot(conn, rid: int, form: dict | None, today: date) -> tuple[float | None, str | None]:
    bench = paces.best_benchmark(conn, rid, today)
    if bench:
        return bench["vdot"], f"{bench['distance_km']} km on {bench['date']}"
    recent = (form or {}).get("recent")
    if recent:
        return round(paces.vdot(recent["distance_km"], recent["time_s"]), 1), "recent race in their programme form"
    return None, None


def plan(conn, rid: int, race_name: str = "", start_time: str = "06:00", today: date | None = None) -> dict:
    today = today or date.today()
    races = conn.execute("SELECT * FROM races WHERE runner_id=? AND date>=? ORDER BY date", (rid, today.isoformat())).fetchall()
    if race_name:
        races = [r for r in races if race_name.strip().lower() in r["name"].lower()]
    if not races:
        return {"error": "no upcoming race on file" + (f" matching {race_name!r}" if race_name else "")}
    race = dict(races[0])
    form = intake.latest(conn, rid)
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    days_to_go = (date.fromisoformat(race["date"]) - today).days
    out = {"race": race["name"], "date": race["date"], "days_to_go": days_to_go, "distance_km": race["distance_km"],
           "climb_m": race["ascent_m"]}
    lib = racelib.info(race["preset_id"]) if race["preset_id"] else None
    if lib:
        out["course_guide"] = racelib.guide(lib["id"])
    vdot, vdot_from = _vdot(conn, rid, form, today)
    if vdot is None:
        out["no_paces_because"] = "no race or time trial on file; a flat all-out 5 km would give them"
        return out
    flat = paces.race_time(vdot, race["distance_km"])
    out.update({"vdot": vdot, "vdot_from": vdot_from, "predicted_finish_flat": _fmt_time(flat),
                "goal_pace_flat": stats.format_pace(flat / race["distance_km"]),
                "assumes": "trained for the distance, flat course, cool conditions"})
    if lib and (splits := racelib.pacing(lib["id"], vdot, race["date"])):
        out["personal_splits"] = splits
    climb_per_km = (race["ascent_m"] or 0) / race["distance_km"]
    if climb_per_km >= 5:
        out["hills"] = (f"{race['ascent_m']:g} m of climb: the flat time is a best case; run the climbs by effort "
                        "(heart rate and breathing), not pace, and expect to finish slower")
    # race-day weather
    spot = None
    if lib and lib["start"]:
        spot = {"name": lib["name"], "latitude": lib["start"]["latitude"], "longitude": lib["start"]["longitude"],
                "elevation_m": None}
    elif runner["latitude"] is not None:
        spot = {"name": runner["location_name"], "latitude": runner["latitude"], "longitude": runner["longitude"],
                "elevation_m": runner["elevation_m"]}
    if days_to_go > FORECAST_DAYS:
        out["race_day_weather"] = (f"forecast available from {date.fromisoformat(race['date']) - timedelta(days=FORECAST_DAYS)}; "
                                   "ask again then for weather-adjusted pacing")
    elif spot is None:
        out["race_day_weather"] = "no race location: save their usual location (set_my_location) for a forecast"
    else:
        try:
            when = datetime.fromisoformat(f"{race['date']}T{start_time}")
            w = truepace.forecast(spot["latitude"], spot["longitude"], when)
            elev = spot["elevation_m"] if spot["elevation_m"] is not None else (w["elevation_m"] or 0)
            s = truepace.slowdown(w["temp_c"], w["dew_point_c"], elev, vdot)
            f = 1 + s["total_pct"] / 100
            out["race_day_weather"] = {"forecast_for": w["time"], "place": spot["name"], "temp_c": w["temp_c"],
                                       "dew_point_c": w["dew_point_c"], "elevation_m": round(elev), "slowdown": s,
                                       "warnings": truepace.warnings(w["temp_c"], w["dew_point_c"], s["total_pct"])}
            out["predicted_finish_race_day"] = _fmt_time(flat * f)
            out["goal_pace_race_day"] = stats.format_pace(flat * f / race["distance_km"])
            if out.get("personal_splits"):
                out["splits_note"] = f"the personal splits are flat-weather numbers: add {s['total_pct']:g}% to each"
        except truepace.WeatherError as e:
            out["race_day_weather"] = f"no forecast ({e})"
    return out
