"""The runner's pace and heart rate profile, recalculated from their data on every call.

Two sources, kept separate so Sarah can say where a number comes from:
- Performance: the best recent time trial or race gives a VDOT (Jack Daniels' running
  formula), which gives training paces and race-time estimates.
- Observed: what the runner's own easy runs have actually looked like (pace at heart rate).
New benchmarks and runs change both, so the profile updates as more running is done.
"""
import json
import math
from datetime import date, timedelta
from statistics import median

from . import stats
from .db import rows

ZONE_NAMES = ["Recovery", "Easy", "Aerobic", "Threshold", "Anaerobic"]
BENCHMARK_DAYS = 120           # a time trial older than this is too stale for paces
OBSERVED_WEEKS = 6
RIEGEL = 1.06                  # race time grows as distance ** 1.06
RACES = {"5K": 5.0, "10K": 10.0, "Half marathon": 21.0975, "Marathon": 42.195}

# Training intensities as a fraction of VDOT (Daniels).
PACE_BANDS = {
    "easy": (0.59, 0.74),
    "marathon": (0.75, 0.84),
    "threshold": (0.83, 0.88),
    "interval": (0.95, 1.00),
}


def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distance_km: float, time_s: float) -> float:
    t = time_s / 60
    return _vo2(distance_km * 1000 / t) / _pct_max(t)


def _speed_for_vo2(vo2: float) -> float:
    """Inverse of _vo2: metres per minute."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def pace_at(vdot_: float, fraction: float) -> float:
    """Seconds per km at a fraction of VDOT."""
    return 60_000 / _speed_for_vo2(vdot_ * fraction)


def zones(runner) -> list[dict]:
    tops = json.loads(runner["hr_zones"]) if runner["hr_zones"] else stats.default_zones(runner["max_hr"] or 190)
    lows = [None] + [t + 1 for t in tops]
    highs = tops + [runner["max_hr"]]
    return [{"zone": i + 1, "name": ZONE_NAMES[i], "from": lows[i], "to": highs[i]} for i in range(5)]


def best_benchmark(conn, rid: int, today: date) -> dict | None:
    since = (today - timedelta(days=BENCHMARK_DAYS)).isoformat()
    best = None
    for r in rows(conn.execute(
            "SELECT date, distance_km, duration_s, run_type FROM runs WHERE runner_id=? AND date>=?"
            " AND run_type IN ('benchmark','race') AND distance_km>=3 AND duration_s>0 AND treadmill=0",
            (rid, since))):
        v = vdot(r["distance_km"], r["duration_s"])
        if best is None or v > best["vdot"]:
            best = {**r, "vdot": round(v, 1)}
    return best


def observed_easy(conn, runner, today: date) -> dict | None:
    """Median pace and HR of recent outdoor easy and long runs that stayed in the easy zones."""
    zs = json.loads(runner["hr_zones"]) if runner["hr_zones"] else None
    ceiling = runner["easy_hr_cap"] or (zs[1] if zs else None)
    if not ceiling:
        return None
    runs = rows(conn.execute(
        "SELECT avg_pace_s_km, avg_hr FROM runs WHERE runner_id=? AND date>=? AND treadmill=0"
        " AND run_type IN ('easy','long') AND avg_hr IS NOT NULL AND avg_hr<=? AND distance_km>=3",
        (runner["runner_id"], (today - timedelta(weeks=OBSERVED_WEEKS)).isoformat(), ceiling)))
    if len(runs) < 2:
        return {"runs": len(runs), "enough_data": False, "hr_ceiling": ceiling}
    return {"runs": len(runs), "enough_data": True, "hr_ceiling": ceiling,
            "pace": stats.format_pace(median(r["avg_pace_s_km"] for r in runs)),
            "avg_hr": round(median(r["avg_hr"] for r in runs))}


def profile(conn, rid: int, today: date | None = None) -> dict:
    today = today or date.today()
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    out = {
        "hr_zones": zones(runner),
        "easy_hr_cap": runner["easy_hr_cap"],
        "zones_set_by_coach": bool(runner["zones_locked"]),
        "observed_easy": observed_easy(conn, runner, today),
    }
    bench = best_benchmark(conn, rid, today)
    if bench is None:
        out["performance"] = {"available": False,
                              "how_to_get_it": "a flat all-out 5K time trial (or a race) of 3 km or more"}
        return out
    v = bench["vdot"]
    t = bench["duration_s"]
    out["performance"] = {
        "available": True,
        "based_on": {"date": bench["date"], "distance_km": bench["distance_km"],
                     "time": _fmt_time(t),
                     "type": bench["run_type"], "age_days": (today - date.fromisoformat(bench["date"])).days},
        "vdot": v,
        "training_paces": {
            name: {"from": stats.format_pace(pace_at(v, hi)), "to": stats.format_pace(pace_at(v, lo))}
            for name, (lo, hi) in PACE_BANDS.items()},
        "race_estimates": {
            name: _fmt_time(t * (km / bench["distance_km"]) ** RIEGEL) for name, km in RACES.items()},
    }
    return out


def _fmt_time(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
