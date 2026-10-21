"""Strength review against the evidence base (ACSM 2026 resistance training position stand).

The numbers here are what the data can show: weekly sets per muscle group, how often each
group was trained, and whether each lift is progressing. Reps in reserve, rest times and
technique are not in the data; Sarah asks about those rather than guessing.
"""
import re
from datetime import date, timedelta

from .db import rows

# Evidence-based starting points (practical guides, not rigid rules).
WEEKLY_SETS_LOW, WEEKLY_SETS_HIGH = 10, 12   # challenging sets per muscle per week
FREQ_TARGET = 2                              # sessions per muscle per week
REP_RANGE = (5, 30)                          # builds muscle when sets are hard enough
STALL_SESSIONS = 4                           # same top weight and reps this many sessions

# Keyword -> muscle groups. First match wins; order matters (e.g. "leg curl" before "curl").
MUSCLES = [
    ("leg curl", ["hamstrings"]), ("hamstring", ["hamstrings"]), ("leg extension", ["quads"]),
    ("leg press", ["quads", "glutes"]), ("squat", ["quads", "glutes"]), ("lunge", ["quads", "glutes"]),
    ("split squat", ["quads", "glutes"]), ("step up", ["quads", "glutes"]),
    ("romanian", ["hamstrings", "glutes"]), ("rdl", ["hamstrings", "glutes"]),
    ("deadlift", ["hamstrings", "glutes", "back"]), ("hip thrust", ["glutes"]), ("glute", ["glutes"]),
    ("calf", ["calves"]), ("bench", ["chest", "triceps"]), ("chest", ["chest", "triceps"]),
    ("push up", ["chest", "triceps"]), ("push-up", ["chest", "triceps"]), ("fly", ["chest"]),
    ("pulldown", ["back", "biceps"]), ("pull up", ["back", "biceps"]), ("pull-up", ["back", "biceps"]),
    ("chin", ["back", "biceps"]), ("row", ["back", "biceps"]),
    ("shoulder press", ["shoulders", "triceps"]), ("overhead press", ["shoulders", "triceps"]),
    ("lateral raise", ["shoulders"]), ("face pull", ["shoulders"]),
    ("dip", ["triceps", "chest"]), ("tricep", ["triceps"]), ("pushdown", ["triceps"]),
    ("curl", ["biceps"]),
    ("plank", ["core"]), ("crunch", ["core"]), ("sit up", ["core"]), ("dead bug", ["core"]),
    ("ab ", ["core"]), ("core", ["core"]),
]


def muscles_for(exercise: str) -> list[str]:
    e = exercise.lower()
    for key, groups in MUSCLES:
        # Whole words at the start ("chin" must not match "machine"); plural endings allowed.
        if re.search(rf"\b{re.escape(key.strip())}", e):
            return groups
    return ["other"]


def _sessions(conn, rid: int, since: str) -> list[dict]:
    out = rows(conn.execute(
        "SELECT s.id, s.date FROM strength_sessions s WHERE s.runner_id=? AND s.date>=? ORDER BY s.date",
        (rid, since)))
    for s in out:
        s["sets"] = rows(conn.execute(
            "SELECT exercise, reps, weight_kg, duration_s FROM strength_sets WHERE session_id=?", (s["id"],)))
    return out


def weekly_volume(sessions: list[dict]) -> dict:
    """Hard sets and training days per muscle group. Every logged working set counts once
    for each group it trains (the primary group of a compound lift counts in full too)."""
    sets, days = {}, {}
    for s in sessions:
        for st in s["sets"]:
            for m in muscles_for(st["exercise"]):
                sets[m] = sets.get(m, 0) + 1
                days.setdefault(m, set()).add(s["date"])
    out = {}
    for m in sorted(sets):
        n, f = sets[m], len(days[m])
        out[m] = {"sets": n, "days": f,
                  # Above the starting range is not wrong, but returns diminish as volume rises.
                  "volume": "below start range" if n < WEEKLY_SETS_LOW
                  else "in start range" if n <= WEEKLY_SETS_HIGH else "above start range",
                  "frequency": "once" if f == 1 else f"{f}x"}
    return out


def lift_progress(conn, rid: int, since: str) -> list[dict]:
    """Per exercise: top weight and best reps at that weight, session by session."""
    hist = rows(conn.execute(
        "SELECT s.date, t.exercise, t.weight_kg, t.reps FROM strength_sets t"
        " JOIN strength_sessions s ON s.id=t.session_id WHERE t.runner_id=? AND s.date>=?"
        " ORDER BY t.exercise, s.date", (rid, since)))
    by_ex: dict[str, dict[str, tuple]] = {}
    for h in hist:
        if h["weight_kg"] is None and h["reps"] is None:
            continue
        sess = by_ex.setdefault(h["exercise"], {})
        top = sess.get(h["date"], (None, 0))
        w, r = h["weight_kg"] or 0, h["reps"] or 0
        if top[0] is None or w > top[0] or (w == top[0] and r > top[1]):
            sess[h["date"]] = (w, r)
    out = []
    for ex, sess in by_ex.items():
        dates = sorted(sess)
        last = sess[dates[-1]]
        item = {"exercise": ex, "muscles": muscles_for(ex), "sessions": len(dates), "last_date": dates[-1],
                "top_kg": last[0] or None, "reps_at_top": last[1]}
        if len(dates) < 2:
            item["trend"] = "first session"
        else:
            prev = sess[dates[-2]]
            item["previous"] = {"kg": prev[0] or None, "reps": prev[1]}
            item["trend"] = ("load up" if last[0] > prev[0] else "load down" if last[0] < prev[0]
                             else "reps up" if last[1] > prev[1] else "reps down" if last[1] < prev[1] else "same")
            same = 1
            for d in reversed(dates[:-1]):
                if sess[d] == last:
                    same += 1
                else:
                    break
            item["same_sessions_in_a_row"] = same
            item["stalled"] = same >= STALL_SESSIONS
        if last[1] and last[1] > REP_RANGE[1]:
            item["note"] = "above 30 reps: adding load would make sets challenging again"
        out.append(item)
    return out


def review(conn, rid: int, today: date | None = None) -> dict:
    today = today or date.today()
    week = _sessions(conn, rid, (today - timedelta(days=6)).isoformat())
    return {
        "guide": {"weekly_sets_per_muscle": f"{WEEKLY_SETS_LOW}-{WEEKLY_SETS_HIGH} to start",
                  "frequency": "about twice a week per muscle", "reps": "5-30, finishing 1-3 reps in reserve",
                  "progression": "add reps within the range, then add load once the top of the range is "
                                 "beaten consistently with good technique",
                  "rest": "2-3 min compound lifts, 1-2 min isolation",
                  "deload": "only when fatigue, falling performance or poor recovery call for it"},
        "last_7_days": {"sessions": len(week), "by_muscle": weekly_volume(week)},
        "lifts": lift_progress(conn, rid, (today - timedelta(weeks=12)).isoformat()),
        "not_in_data": ["reps in reserve", "rest times", "technique", "sleep and protein"],
    }
