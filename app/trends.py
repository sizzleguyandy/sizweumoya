"""Trends and progression from the person's own training, with or without a programme.

- run_context: how one run compares with their similar recent runs, and any personal bests.
- progress: are they getting fitter? Four running signals, a status (green / yellow / grey,
  never red) and stagnation flags.
- load: the weekly load and injury traffic light (green / amber / red) from the spec.
- taper: race-week targets from their own recent mileage when a race is close.
- strength_records: best weights and estimated 1RM per lift.
All numbers come from the database; Sarah words them.
"""
import json
from datetime import date, timedelta
from statistics import mean

from . import stats
from .db import rows

PB_DISTANCES = (5.0, 10.0, 21.1, 42.2)   # best pace over runs at least this long


def _weeks(conn, rid: int, end: date, n: int) -> list[float]:
    """km per Monday-Sunday week for the n weeks before the week containing `end` (oldest first)."""
    monday = end - timedelta(days=end.weekday())
    out = []
    for i in range(n, 0, -1):
        a = monday - timedelta(weeks=i)
        km = conn.execute("SELECT COALESCE(SUM(distance_km),0) FROM runs WHERE runner_id=? AND date>=? AND date<?",
                          (rid, a.isoformat(), (a + timedelta(days=7)).isoformat())).fetchone()[0]
        out.append(round(km, 1))
    return out


def _careful(conn, rid: int) -> bool:
    """Careful mode: an active health note (injury or niggle) on file."""
    return bool(conn.execute("SELECT 1 FROM coach_notes WHERE runner_id=? AND category='health' AND status='active'",
                             (rid,)).fetchone())


# ---- one run in context ----------------------------------------------------------

def run_context(conn, rid: int, run_date: str, distance_km: float, avg_pace: float | None,
                avg_hr: float | None, run_type: str, treadmill: bool = False) -> dict:
    """Compare a run with similar runs (same type, distance within 20%, outdoor, last 8 weeks)."""
    out: dict = {}
    since = (date.fromisoformat(run_date) - timedelta(weeks=8)).isoformat()
    similar = rows(conn.execute(
        "SELECT avg_pace_s_km, avg_hr FROM runs WHERE runner_id=? AND date>=? AND date<? AND treadmill=0"
        " AND run_type=? AND distance_km BETWEEN ? AND ? AND avg_pace_s_km IS NOT NULL",
        (rid, since, run_date, run_type, distance_km * 0.8, distance_km * 1.2)))
    if len(similar) >= 2 and avg_pace and not treadmill:
        p = mean(r["avg_pace_s_km"] for r in similar)
        hrs = [r["avg_hr"] for r in similar if r["avg_hr"]]
        out["vs_similar"] = {
            "runs_compared": len(similar),
            "their_usual_pace": stats.format_pace(p),
            "pace_diff_s_km": round(avg_pace - p),            # negative = faster than usual
            "their_usual_hr": round(mean(hrs)) if hrs else None,
            "hr_diff": round(avg_hr - mean(hrs)) if hrs and avg_hr else None,
        }
    # Personal bests (outdoor only)
    pbs = []
    longest = conn.execute("SELECT MAX(distance_km) FROM runs WHERE runner_id=? AND date<?",
                           (rid, run_date)).fetchone()[0]
    if longest is not None and distance_km > longest:
        pbs.append({"type": "longest_run", "km": round(distance_km, 2), "previous": round(longest, 2)})
    if avg_pace and not treadmill:
        for d in PB_DISTANCES:
            if distance_km >= d:
                best = conn.execute(
                    "SELECT MIN(avg_pace_s_km) FROM runs WHERE runner_id=? AND date<? AND distance_km>=?"
                    " AND treadmill=0", (rid, run_date, d)).fetchone()[0]
                if best is not None and avg_pace < best:
                    pbs.append({"type": f"fastest_{d:g}k_plus", "pace": stats.format_pace(avg_pace),
                                "previous": stats.format_pace(best)})
    if pbs:
        out["personal_bests"] = pbs
    count = conn.execute("SELECT COUNT(*) FROM runs WHERE runner_id=? AND date<=?", (rid, run_date)).fetchone()[0]
    if count in (10, 25, 50, 100, 200, 500):
        out["milestone"] = f"run number {count}"
    return out


# ---- progress --------------------------------------------------------------------

def _trend(older: float, newer: float, tolerance: float) -> str:
    if older <= 0 and newer <= 0:
        return "none"
    if newer > older * (1 + tolerance):
        return "rising"
    if newer < older * (1 - tolerance):
        return "falling"
    return "flat"


def progress(conn, rid: int, today: date | None = None) -> dict:
    """Is the person progressing? Signals over the last 12 weeks, a status and stagnation flags."""
    from . import queries  # fitness_trend lives there
    today = today or date.today()
    weeks = _weeks(conn, rid, today, 12)
    recent4, prev4 = weeks[-4:], weeks[-8:-4]
    longest = lambda a, b: conn.execute(
        "SELECT COALESCE(MAX(distance_km),0) FROM runs WHERE runner_id=? AND date>=? AND date<?",
        (rid, a.isoformat(), b.isoformat())).fetchone()[0]
    monday = today - timedelta(days=today.weekday())
    long_recent = longest(monday - timedelta(weeks=4), monday)
    long_prev = longest(monday - timedelta(weeks=8), monday - timedelta(weeks=4))
    zones = rows(conn.execute(
        "SELECT COALESCE(SUM(z1_s+z2_s),0) e, COALESCE(SUM(z1_s+z2_s+z3_s+z4_s+z5_s),0) t FROM runs"
        " WHERE runner_id=? AND date>=?", (rid, (monday - timedelta(weeks=4)).isoformat())))[0]
    fit = queries.fitness_trend(conn, rid, today, save=False)

    signals = {
        "fitness": {"verdict": fit["verdict"], "pace_at_hr_now": fit["pace"], "before": fit["previous_pace"],
                    "ref_hr": fit["ref_hr"]},
        "weekly_km": {"last_4_weeks_avg": round(mean(recent4), 1), "previous_4_avg": round(mean(prev4), 1),
                      "trend": _trend(mean(prev4), mean(recent4), 0.10), "weeks": weeks},
        "longest_run": {"last_4_weeks": round(long_recent, 1), "previous_4": round(long_prev, 1),
                        "trend": _trend(long_prev, long_recent, 0.05)},
        "easy_share_pct": round(100 * zones["e"] / zones["t"]) if zones["t"] else None,
    }
    stagnation = []
    active = [w for w in weeks[-6:] if w > 0]
    if len(active) == 6 and max(active) - min(active) <= 0.1 * mean(active):
        stagnation.append("weekly distance flat for 6 weeks")
    six = longest(monday - timedelta(weeks=6), monday)
    before = longest(monday - timedelta(weeks=12), monday - timedelta(weeks=6))
    if six and before and six <= before:
        stagnation.append("longest run hasn't grown in 6 weeks")
    if signals["easy_share_pct"] is not None and signals["easy_share_pct"] < 60:
        stagnation.append("most running is moderate or hard (little truly easy running)")
    # One check per month (the last one), so extra checks on request don't count twice.
    recent_checks = [r["verdict"] for r in conn.execute(
        "SELECT verdict FROM fitness_checks f WHERE runner_id=? AND verdict!='insufficient' AND id ="
        " (SELECT MAX(id) FROM fitness_checks g WHERE g.runner_id=f.runner_id"
        "  AND substr(g.date,1,7)=substr(f.date,1,7)) ORDER BY date DESC LIMIT 3", (rid,))]
    if len(recent_checks) == 3 and all(v == "steady" for v in recent_checks):
        stagnation.append("easy pace at the same heart rate unchanged for 3 checks")

    if fit["verdict"] == "fitter" or (signals["weekly_km"]["trend"] == "rising"
                                     and signals["longest_run"]["trend"] in ("rising", "flat")
                                     and fit["verdict"] != "dipping"):
        status = "green"
    elif fit["verdict"] == "dipping" or (signals["weekly_km"]["trend"] == "falling" and fit["verdict"] != "fitter"):
        status = "grey"
    else:
        status = "yellow"
    return {"status": status, "signals": signals, "stagnation": stagnation}


# ---- load and injury traffic light ------------------------------------------------

def load(conn, rid: int, week_start: date) -> dict:
    """Traffic light for the week starting week_start (Monday). Rules from the spec."""
    end = week_start + timedelta(days=7)
    this = conn.execute("SELECT COALESCE(SUM(distance_km),0) FROM runs WHERE runner_id=? AND date>=? AND date<?",
                        (rid, week_start.isoformat(), end.isoformat())).fetchone()[0]
    prev = _weeks(conn, rid, week_start, 4)          # the 4 weeks before this one
    last, avg4 = prev[-1], mean(prev)
    careful = _careful(conn, rid)
    limit = 5 if careful else 10
    signs = []
    recent_normal = max(prev[-4:-1]) if len(prev) >= 4 else 0
    returning = last < 0.8 * avg4 and this <= recent_normal * 1.05  # back to normal after a down week
    if last and this - last >= 5 and 100 * (this - last) / last > limit and not returning:
        signs.append(f"weekly distance up {round(100 * (this - last) / last)}% on last week")
    if avg4 and this >= 5 and this > 1.5 * avg4 and not returning:
        signs.append("this week is more than 1.5x the 4-week average")
    days = {r[0] for r in conn.execute(
        "SELECT date FROM runs WHERE runner_id=? AND date>=? UNION SELECT date FROM strength_sessions"
        " WHERE runner_id=? AND date>=?", (rid, (end - timedelta(days=14)).isoformat(), rid,
                                           (end - timedelta(days=14)).isoformat()))}
    streak, best, d = 0, 0, end - timedelta(days=14)
    while d < end:
        streak = streak + 1 if d.isoformat() in days else 0
        best = max(best, streak)
        d += timedelta(days=1)
    if best >= 7:
        signs.append(f"{best} days in a row without a rest day")
    z = rows(conn.execute("SELECT COALESCE(SUM(z1_s+z2_s),0) e, COALESCE(SUM(z1_s+z2_s+z3_s+z4_s+z5_s),0) t"
                          " FROM runs WHERE runner_id=? AND date>=? AND date<?",
                          (rid, week_start.isoformat(), end.isoformat())))[0]
    if z["t"] >= 3600 and z["e"] / z["t"] < 0.70:
        signs.append(f"only {round(100 * z['e'] / z['t'])}% of running was easy")
    long_now = conn.execute("SELECT COALESCE(MAX(distance_km),0) FROM runs WHERE runner_id=? AND date>=? AND date<?",
                            (rid, week_start.isoformat(), end.isoformat())).fetchone()[0]
    long_before = conn.execute("SELECT COALESCE(MAX(distance_km),0) FROM runs WHERE runner_id=? AND date>=? AND date<?",
                               (rid, (week_start - timedelta(weeks=6)).isoformat(), week_start.isoformat())).fetchone()[0]
    if long_before >= 5 and long_now > 1.15 * long_before:
        signs.append(f"long run {round(long_now, 1)} km, more than 15% past the longest of the last 6 weeks")
    # Heart rate higher at the same easy pace: last 2 weeks vs the 4 before
    easy = lambda a, b: rows(conn.execute(
        "SELECT avg_pace_s_km p, avg_hr h FROM runs WHERE runner_id=? AND is_easy_valid=1 AND date>=? AND date<?",
        (rid, a.isoformat(), b.isoformat())))
    now, base = easy(end - timedelta(weeks=2), end), easy(end - timedelta(weeks=6), end - timedelta(weeks=2))
    if len(now) >= 2 and len(base) >= 3:
        hr_per_pace = lambda rs: mean(r["h"] / (1000 / r["p"]) for r in rs)  # bpm per m/s
        if hr_per_pace(now) > 1.04 * hr_per_pace(base):
            signs.append("heart rate running higher than usual at the same easy pace")
    niggle = careful and this > 0
    if niggle and signs:
        level = "red"
    elif len(signs) >= 3:
        level = "red"
    elif signs:
        level = "amber"
    else:
        level = "green"
    return {"level": level, "signs": signs, "careful_mode": careful, "week_km": round(this, 1),
            "last_week_km": last, "four_week_avg_km": round(avg4, 1)}


# ---- taper -------------------------------------------------------------------

TAPER = {  # days out the taper starts, then % of base for week -3, -2 and race week
    "5k": (7, None, None, 70), "10k": (10, None, 85, 60), "half": (14, None, 75, 50),
    "marathon": (21, 80, 65, 40),
}


def _race_kind(km: float) -> str:
    return "5k" if km <= 6 else "10k" if km <= 12 else "half" if km <= 25 else "marathon"


def taper(conn, rid: int, today: date | None = None) -> dict | None:
    """Suggested taper for the next race if it's within 4 weeks. Base = average of the last 3 full
    weeks before the taper, ignoring unusually light weeks (under 60% of the others)."""
    today = today or date.today()
    race = conn.execute("SELECT name, date, distance_km FROM races WHERE runner_id=? AND date>=? ORDER BY date LIMIT 1",
                        (rid, today.isoformat())).fetchone()
    if not race or not race["distance_km"]:
        return None
    rdate = date.fromisoformat(race["date"])
    days = (rdate - today).days
    if days > 28:
        return None
    kind = _race_kind(race["distance_km"])
    start_days, w3, w2, rw = TAPER[kind]
    taper_start = rdate - timedelta(days=start_days)
    weeks = _weeks(conn, rid, min(today, taper_start), 3)
    usable = [w for w in weeks if w >= 0.6 * max(weeks)] if max(weeks) else []
    base = round(mean(usable), 1) if usable else 0
    careful = _careful(conn, rid)
    short = base < 20
    f = 0.9 if careful else 1.0
    weeks_out = (days + 6) // 7
    plan = []
    if w3:
        plan.append({"week": "3 weeks out", "target_km": round(base * w3 / 100 * f)})
    if w2:
        plan.append({"week": "2 weeks out", "target_km": round(base * w2 / 100 * f)})
    plan.append({"week": "race week (excluding the race)", "target_km": round(base * rw / 100 * f)})
    return {"race": dict(race), "days_to_go": days, "weeks_to_go": weeks_out, "race_type": kind,
            "taper_starts": taper_start.isoformat(), "in_taper": today >= taper_start,
            "base_km_per_week": base, "plan": plan, "careful_mode": careful,
            "low_base_note": "base under 20 km a week: a shorter, gentler taper is enough" if short else None,
            "keep": "1-2 short race-pace efforts each taper week; cut distance, not all intensity"}


# ---- strength records ----------------------------------------------------------

def epley(weight: float, reps: int) -> float:
    return weight * (1 + reps / 30) if reps and reps > 1 else weight


def strength_records(conn, rid: int, since_days: int = 30) -> list[dict]:
    """Best weight and best estimated 1RM per lift, and whether either was set recently."""
    cutoff = (date.today() - timedelta(days=since_days)).isoformat()
    out = []
    for ex in [r[0] for r in conn.execute("SELECT DISTINCT exercise FROM strength_sets WHERE runner_id=?"
                                          " AND weight_kg>0", (rid,))]:
        sets = rows(conn.execute(
            "SELECT s.date, t.weight_kg w, t.reps r FROM strength_sets t JOIN strength_sessions s ON s.id=t.session_id"
            " WHERE t.runner_id=? AND t.exercise=? AND t.weight_kg>0", (rid, ex)))
        top = max(sets, key=lambda x: (x["w"], x["r"] or 0))
        e1 = max(sets, key=lambda x: epley(x["w"], x["r"] or 1))
        had_before = any(x["date"] < cutoff for x in sets)
        out.append({"exercise": ex, "best_kg": top["w"], "best_kg_reps": top["r"], "best_kg_date": top["date"],
                    "est_1rm_kg": round(epley(e1["w"], e1["r"] or 1), 1), "est_1rm_date": e1["date"],
                    "new_record": had_before and (top["date"] >= cutoff or e1["date"] >= cutoff)})
    return out
