"""Read/write helpers. Every function takes runner_id from the caller's resolved identity."""
import secrets
from datetime import date, timedelta

import json

from . import paces, plans, stats, trends
from . import strength as strength_mod
from .db import rows

NOTE_CATEGORIES = {"pattern", "preference", "health", "life", "motivation", "milestone", "profile", "gear"}


def profile(conn, rid: int) -> dict:
    r = conn.execute(
        "SELECT name, max_hr, resting_hr, hr_zones, easy_hr_cap, weight_kg, goal, units, tier, joined FROM runners WHERE runner_id=?",
        (rid,),
    ).fetchone()
    return dict(r) if r else {}


def recent_runs(conn, rid: int, n: int = 5) -> list[dict]:
    out = rows(conn.execute(
        "SELECT activity_key, source, date, name, note, treadmill, distance_km, duration_s, avg_pace_s_km,"
        " avg_hr, max_hr, z1_s, z2_s, z3_s, z4_s, z5_s, elevation_m, hr_drift_pct, run_type FROM runs"
        " WHERE runner_id=? ORDER BY date DESC LIMIT ?",
        (rid, max(1, min(n, 30))),
    ))
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    for r in out:
        r["pace"] = stats.format_pace(r["avg_pace_s_km"])
        r["tips"] = stats.run_tips(r)
        r["plan"] = plans.plan_for_run(conn, runner, r.pop("activity_key"), r)
        cap = (r["plan"] or {}).get("hr_cap") or (
            runner["easy_hr_cap"] if r["run_type"] in ("easy", "long") else None)
        if cap and r["avg_hr"] and r["avg_hr"] > cap and r["run_type"] in ("easy", "long"):
            r["tips"].append("over_hr_cap")
    return out


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_detail(conn, rid: int, start: date, end: date) -> dict:
    """Time on feet, time in each HR zone (named, with bpm), longest run, easy runs over the cap."""
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    runs = rows(conn.execute(
        "SELECT distance_km, duration_s, avg_hr, run_type, z1_s, z2_s, z3_s, z4_s, z5_s FROM runs"
        " WHERE runner_id=? AND date>=? AND date<?", (rid, start.isoformat(), end.isoformat())))
    secs = [sum(r[f"z{i}_s"] or 0 for r in runs) for i in range(1, 6)]
    total_z = sum(secs)
    zones = []
    for z, s_ in zip(paces.zones(runner), secs):
        zones.append({**z, "minutes": round(s_ / 60), "pct": round(100 * s_ / total_z) if total_z else 0})
    cap = runner["easy_hr_cap"]
    easy_runs = [r for r in runs if r["run_type"] in ("easy", "long") and r["avg_hr"]]
    return {
        "total_time": paces._fmt_time(sum(r["duration_s"] or 0 for r in runs)) if runs else "0:00",
        "longest_run_km": round(max((r["distance_km"] or 0 for r in runs), default=0), 1),
        "time_in_zones": zones,
        # Runs imported from a spreadsheet have no zone detail, so they are not in time_in_zones.
        "runs_without_zone_detail": sum(1 for r in runs if not any(r[f"z{i}_s"] for i in range(1, 6))),
        "easy_runs_over_hr_cap": (sum(1 for r in easy_runs if r["avg_hr"] > cap), len(easy_runs)) if cap else None,
    }


def _bar(value: float, target: float, width: int = 10) -> str:
    filled = 0 if not target else max(0, min(width, round(width * value / target)))
    return "\u25b0" * filled + "\u25b1" * (width - filled)


LOAD_WORD = {"green": "Green (all good)", "amber": "Amber (keep an eye on it)", "red": "Red (ease off)"}


def week_card(w: dict, start: date, today: date | None = None) -> str:
    """The short graphic at the top of "My week": the same layout every time, built here so the
    numbers are always the system's, never retyped by the model."""
    today = today or date.today()
    end = start + timedelta(days=6)
    day = min(7, max(1, (today - start).days + 1))
    head = f"MY WEEK  {start.day} {start:%b} - {end.day} {end:%b}" + (f"  (day {day} of 7)" if day < 7 else "")
    lines = [head]
    if w.get("focus") in ("running", "hybrid"):
        vs = w.get("vs_programme") or {}
        planned = vs.get("planned_km")
        ref = planned or w.get("last_week_km") or 0
        ref_text = f"of {planned:g} km planned" if planned else (f"last week {ref:g} km" if ref else "")
        lines.append(f"Run       {w['total_km']:g} km  {_bar(w['total_km'], ref)}  {ref_text}".rstrip())
        lines.append(f"          {w['runs']} run{'s' if w['runs'] != 1 else ''} · {w.get('total_time', '0:00')}"
                     + (f" · {vs['done']}/{vs['sessions_planned']} sessions done" if planned else ""))
        if w.get("runs") and w.get("time_in_zones") and any(z["minutes"] for z in w["time_in_zones"]):
            lines.append(f"Easy      {w['easy_pct']}% of running time")
    if w.get("focus") in ("strength", "hybrid") or w.get("strength_vs_programme"):
        sv = w.get("strength_vs_programme") or {}
        sets = ((w.get("strength_review") or {}).get("last_7_days") or {}).get("by_muscle") or {}
        total_sets = sum(m["sets"] for m in sets.values())
        if sv.get("sessions_planned"):
            lines.append(f"Strength  {sv['done']} of {sv['sessions_planned']} sessions  "
                         f"{_bar(sv['done'], sv['sessions_planned'], sv['sessions_planned'])}")
        else:
            lines.append(f"Strength  {w['strength_sessions']} session{'s' if w['strength_sessions'] != 1 else ''}"
                         + (f" · {total_sets} sets" if total_sets else ""))
    if w.get("load"):
        lines.append(f"Load      {LOAD_WORD.get(w['load']['level'], w['load']['level'])}")
    if w.get("taper"):
        t = w["taper"]
        lines.append(f"Race      {t['race']['name']} in {t['days_to_go']} days")
    return "\n".join(lines)


def week_summary(conn, rid: int, start: date | None = None) -> dict:
    start = start or week_start(date.today())
    end, prev = start + timedelta(days=7), start - timedelta(days=7)

    def km_and_zones(a: date, b: date):
        r = conn.execute(
            "SELECT COALESCE(SUM(distance_km),0) km, COUNT(*) n, COALESCE(SUM(z1_s),0) z1, COALESCE(SUM(z2_s),0) z2,"
            " COALESCE(SUM(z1_s+z2_s+z3_s+z4_s+z5_s),0) zt FROM runs WHERE runner_id=? AND date>=? AND date<?",
            (rid, a.isoformat(), b.isoformat()),
        ).fetchone()
        return r["km"], r["n"], ((r["z1"] + r["z2"]) / r["zt"]) if r["zt"] else 0.0

    km, n, easy = km_and_zones(start, end)
    last_km, _, _ = km_and_zones(prev, start)
    strength = conn.execute(
        "SELECT COUNT(*) FROM strength_sessions WHERE runner_id=? AND date>=? AND date<?",
        (rid, start.isoformat(), end.isoformat()),
    ).fetchone()[0]
    last_strength = conn.execute(
        "SELECT MAX(date) FROM strength_sessions WHERE runner_id=?", (rid,)
    ).fetchone()[0]
    days_since = (date.today() - date.fromisoformat(last_strength)).days if last_strength else None
    change = stats.km_change_pct(km, last_km)
    result = {
        "week_start": start.isoformat(), "total_km": round(km, 1), "last_week_km": round(last_km, 1),
        "km_change_pct": change, "mileage_verdict": stats.mileage_verdict(change), "runs": n,
        "strength_sessions": strength, "easy_pct": round(easy * 100),
        "tips": stats.week_tips(km, last_km, easy, days_since),
    }
    result.update(_week_detail(conn, rid, start, end))
    m = plans.mode(conn, rid, min(end - timedelta(days=1), date.today()))
    result["focus"] = m["focus"]
    result["coaching_mode"] = m["mode"]
    if m["running"] == "guided":
        result["vs_programme"] = plans.week_vs_programme(conn, rid, start, end, result)
    if m["focus"] in ("strength", "hybrid") or m["strength"] == "guided":
        result["strength_review"] = strength_mod.review(conn, rid, end - timedelta(days=1))
    if m["strength"] == "guided":
        result["strength_vs_programme"] = plans.strength_vs_programme(conn, rid, start, end)
    if m["focus"] in ("running", "hybrid"):
        result["load"] = trends.load(conn, rid, start)
        if m["running"] != "guided":  # a programme from Coach Andy has its own taper
            result["taper"] = trends.taper(conn, rid)
    result["card"] = week_card(result, start)
    conn.execute(
        "INSERT INTO weekly_summary (runner_id, week_start, total_km, runs, strength_sessions, easy_pct, km_change_pct,"
        " flag_level, flags) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(runner_id, week_start) DO UPDATE SET"
        " total_km=excluded.total_km, runs=excluded.runs, strength_sessions=excluded.strength_sessions,"
        " easy_pct=excluded.easy_pct, km_change_pct=excluded.km_change_pct, flag_level=excluded.flag_level,"
        " flags=excluded.flags",
        (rid, result["week_start"], result["total_km"], n, strength, result["easy_pct"], change,
         (result.get("load") or {}).get("level"), json.dumps((result.get("load") or {}).get("signs") or [])),
    )
    conn.commit()
    return result


PROGRESS = {"fitter": "green", "steady": "yellow", "dipping": "grey", "insufficient": "grey"}


def month_summary(conn, rid: int, year: int, month: int) -> dict:
    """Calendar month report: totals, zones, weeks, strength, fitness and, if guided, the programme."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    prev = date(start.year - (start.month == 1), (start.month - 2) % 12 + 1, 1)
    q = ("SELECT COALESCE(SUM(distance_km),0) km, COUNT(*) n FROM runs WHERE runner_id=? AND date>=? AND date<?")
    cur = conn.execute(q, (rid, start.isoformat(), end.isoformat())).fetchone()
    last = conn.execute(q, (rid, prev.isoformat(), start.isoformat())).fetchone()
    weeks, w = [], week_start(start)
    while w < end:
        km = conn.execute(q, (rid, max(w, start).isoformat(), min(w + timedelta(days=7), end).isoformat())).fetchone()
        weeks.append({"week_of": w.isoformat(), "km": round(km["km"], 1), "runs": km["n"]})
        w += timedelta(days=7)
    strength = rows(conn.execute(
        "SELECT COUNT(DISTINCT s.id) sessions, COUNT(t.id) sets FROM strength_sessions s LEFT JOIN strength_sets t"
        " ON t.session_id=s.id WHERE s.runner_id=? AND s.date>=? AND s.date<?",
        (rid, start.isoformat(), end.isoformat())))[0]
    fit = fitness_trend(conn, rid, min(end - timedelta(days=1), date.today()))
    out = {
        "month": start.strftime("%B %Y"),
        "total_km": round(cur["km"], 1), "runs": cur["n"],
        "previous_month_km": round(last["km"], 1),
        "km_change_pct": stats.km_change_pct(cur["km"], last["km"]),
        "weeks": weeks,
        **_week_detail(conn, rid, start, end),
        "benchmarks": rows(conn.execute(
            "SELECT date, distance_km, duration_s, run_type FROM runs WHERE runner_id=? AND date>=? AND date<?"
            " AND run_type IN ('benchmark','race') ORDER BY date", (rid, start.isoformat(), end.isoformat()))),
        "strength": strength,
        "fitness": fit,
        "progress_status": PROGRESS.get(fit["verdict"], "grey"),
    }
    m = plans.mode(conn, rid, min(end - timedelta(days=1), date.today()))
    out["focus"], out["coaching_mode"] = m["focus"], m["mode"]
    if m["running"] == "guided":
        out["vs_programme"] = plans.week_vs_programme(conn, rid, start, end, out)
    if m["focus"] in ("strength", "hybrid"):
        out["lifts"] = strength_mod.lift_progress(conn, rid, start.isoformat())
    if m["strength"] == "guided":
        out["strength_vs_programme"] = plans.strength_vs_programme(conn, rid, start, end)
    if m["focus"] in ("running", "hybrid"):
        out["progress"] = trends.progress(conn, rid, min(end - timedelta(days=1), date.today()))
    if m["focus"] in ("strength", "hybrid"):
        out["strength_records"] = trends.strength_records(conn, rid, since_days=(end - start).days)
    return out


def fitness_trend(conn, rid: int, today: date | None = None, save: bool = True) -> dict:
    today = today or date.today()
    w1, w2 = today - timedelta(days=28), today - timedelta(days=56)
    q = ("SELECT avg_pace_s_km, avg_hr FROM runs WHERE runner_id=? AND is_easy_valid=1"
         " AND avg_hr IS NOT NULL AND date>? AND date<=?")
    recent = rows(conn.execute(q, (rid, w1.isoformat(), today.isoformat())))
    previous = rows(conn.execute(q, (rid, w2.isoformat(), w1.isoformat())))
    res = stats.fitness_check(recent, previous)
    if save:
        conn.execute(
            "INSERT INTO fitness_checks (runner_id, date, ref_hr, pace_s_km, prev_pace_s_km, change_pct, verdict)"
            " VALUES (?,?,?,?,?,?,?)",
            (rid, today.isoformat(), res.ref_hr, res.pace_s_km, res.prev_pace_s_km, res.change_pct, res.verdict),
        )
        conn.commit()
    return {
        "verdict": res.verdict, "ref_hr": res.ref_hr, "pace": stats.format_pace(res.pace_s_km),
        "previous_pace": stats.format_pace(res.prev_pace_s_km), "change_pct": res.change_pct,
        "valid_runs_recent": len(recent), "valid_runs_previous": len(previous),
    }


def baseline(conn, rid: int, weeks: int = 8, today: date | None = None) -> dict:
    """The onboarding snapshot: the last `weeks` weeks of uploaded runs and strength sessions."""
    today = today or date.today()
    since = (today - timedelta(weeks=weeks)).isoformat()
    runs = rows(conn.execute(
        "SELECT date, distance_km, duration_s, avg_pace_s_km, avg_hr, z1_s, z2_s, z3_s, z4_s, z5_s,"
        " is_easy_valid FROM runs WHERE runner_id=? AND date>? ORDER BY date", (rid, since)))
    strength = conn.execute(
        "SELECT COUNT(*) FROM strength_sessions WHERE runner_id=? AND date>?", (rid, since)).fetchone()[0]
    if not runs:
        return {"weeks": weeks, "runs": 0, "strength_sessions": strength,
                "enough_data": False}
    first = date.fromisoformat(runs[0]["date"])
    span_weeks = max(1.0, min(weeks, ((today - first).days + 1) / 7))
    total_km = sum(r["distance_km"] or 0 for r in runs)
    zone_tot = sum(sum(r[f"z{i}_s"] or 0 for i in range(1, 6)) for r in runs)
    easy_s = sum((r["z1_s"] or 0) + (r["z2_s"] or 0) for r in runs)
    easy = [r for r in runs if r["is_easy_valid"]]
    max_hr = conn.execute("SELECT max_hr FROM runners WHERE runner_id=?", (rid,)).fetchone()[0]
    return {
        "weeks": weeks,
        "weeks_with_data": round(span_weeks, 1),
        "runs": len(runs),
        "avg_km_per_week": round(total_km / span_weeks, 1),
        "avg_runs_per_week": round(len(runs) / span_weeks, 1),
        "longest_run_km": round(max(r["distance_km"] or 0 for r in runs), 1),
        "easy_pct": round(100 * easy_s / zone_tot) if zone_tot else None,
        "easy_pace": stats.format_pace(
            sum(r["avg_pace_s_km"] for r in easy) / len(easy)) if easy else None,
        "easy_hr": round(sum(r["avg_hr"] for r in easy) / len(easy)) if easy else None,
        "max_hr_seen": max_hr,
        "strength_sessions": strength,
        "enough_data": len(runs) >= 6,
    }


def lift_history(conn, rid: int, exercise: str, limit: int = 10) -> list[dict]:
    return rows(conn.execute(
        "SELECT s.date, t.exercise, MAX(t.weight_kg) top_kg, SUM(t.reps) total_reps, COUNT(*) sets"
        " FROM strength_sets t JOIN strength_sessions s ON s.id=t.session_id"
        " WHERE t.runner_id=? AND t.exercise LIKE ? GROUP BY s.id ORDER BY s.date DESC LIMIT ?",
        (rid, f"%{exercise}%", max(1, min(limit, 30))),
    ))


def last_strength_session(conn, rid: int) -> dict | None:
    s = conn.execute(
        "SELECT id, date, title, duration_s, parse_ok FROM strength_sessions WHERE runner_id=? ORDER BY date DESC LIMIT 1",
        (rid,),
    ).fetchone()
    if not s:
        return None
    out = dict(s)
    out["sets"] = rows(conn.execute(
        "SELECT exercise, set_no, reps, weight_kg, duration_s FROM strength_sets WHERE session_id=? ORDER BY id",
        (s["id"],),
    ))
    bests = {r[0]: r[1] for r in conn.execute(
        "SELECT t.exercise, MAX(t.weight_kg) FROM strength_sets t JOIN strength_sessions x ON x.id=t.session_id"
        " WHERE t.runner_id=? AND x.date<? AND t.weight_kg>0 GROUP BY t.exercise", (rid, s["date"]))}
    out["card"] = session_card(out, bests)
    return out


def _kg(w) -> str:
    return f"{w:g} kg" if w else "BW"


def _set_text(st: dict) -> str:
    if st["reps"] is not None:
        return str(st["reps"])
    if st["duration_s"]:
        return f"{st['duration_s']}s"
    return "-"


NAME_WIDTH = 24


def session_card(session: dict, previous_best: dict | None = None) -> str:
    """The aligned summary at the top of a strength debrief, built here so every number is
    the system's. One line per exercise: name, weight, reps per set. A weight above the
    person's previous best for that lift is marked "new best"."""
    previous_best = previous_best or {}
    order: list[str] = []
    by_ex: dict[str, list[dict]] = {}
    for st in session["sets"]:
        if st["exercise"] not in by_ex:
            order.append(st["exercise"])
        by_ex.setdefault(st["exercise"], []).append(st)
    d = date.fromisoformat(session["date"])
    total = len(session["sets"])
    head = f"GYM  {d:%a} {d.day} {d:%b}  ·  {len(order)} exercise{'s' if len(order) != 1 else ''}  ·  {total} sets"
    if session.get("duration_s"):
        h, m = divmod(round(session["duration_s"] / 60), 60)
        head += f"  ·  {h}:{m:02d}" if h else f"  ·  {m} min"
    lines = [head, ""]
    for ex in order:
        sets = by_ex[ex]
        name = ex[:1].upper() + ex[1:]
        name = name if len(name) <= NAME_WIDTH else name[:NAME_WIDTH - 1] + "."
        weights = {st["weight_kg"] or 0 for st in sets}
        if len(weights) == 1:
            detail = f"{_kg(sets[0]['weight_kg']):>8}  " + " ".join(_set_text(st) for st in sets)
        else:  # weight changed between sets: show each as reps x weight
            detail = "  ".join(f"{_set_text(st)}x{st['weight_kg'] or 0:g}" for st in sets)
            detail = f"{'':>8}  {detail}"
        top = max(weights)
        if top and ex in previous_best and top > (previous_best[ex] or 0):
            detail += "  new best"
        lines.append(f"{name:<{NAME_WIDTH}}{detail}")
    return "\n".join(lines)


def recent_tips(conn, rid: int, n: int = 5) -> list[dict]:
    return rows(conn.execute(
        "SELECT date, rule, text FROM tips_given WHERE runner_id=? ORDER BY id DESC LIMIT ?", (rid, n)))


def record_tip(conn, rid: int, rule: str, text: str) -> None:
    conn.execute("INSERT INTO tips_given (runner_id, rule, text) VALUES (?,?,?)", (rid, rule, text))
    conn.commit()


def notes_and_summary(conn, rid: int) -> dict:
    s = conn.execute("SELECT summary, updated FROM runner_summary WHERE runner_id=?", (rid,)).fetchone()
    notes = rows(conn.execute(
        "SELECT id, date, category, note, review_by FROM coach_notes WHERE runner_id=? AND status='active'"
        " ORDER BY date DESC LIMIT 30", (rid,)))
    return {"summary": dict(s) if s else None, "active_notes": notes}


def add_note(conn, rid: int, category: str, note: str, source: str = "chat",
             review_by: str | None = None) -> int:
    if category not in NOTE_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(NOTE_CATEGORIES)}")
    cur = conn.execute(
        "INSERT INTO coach_notes (runner_id, source, category, note, review_by) VALUES (?,?,?,?,?)",
        (rid, source, category, note[:1000], review_by),
    )
    conn.commit()
    return cur.lastrowid


def set_note_status(conn, rid: int, note_id: int, status: str) -> bool:
    if status not in ("resolved", "outdated"):
        raise ValueError("status must be resolved or outdated")
    cur = conn.execute("UPDATE coach_notes SET status=? WHERE id=? AND runner_id=?", (status, note_id, rid))
    conn.commit()
    return cur.rowcount == 1


def save_summary(conn, rid: int, summary: str) -> None:
    conn.execute(
        "INSERT INTO runner_summary (runner_id, summary) VALUES (?,?) ON CONFLICT(runner_id) DO UPDATE"
        " SET summary=excluded.summary, updated=datetime('now')", (rid, summary[:3000]))
    conn.commit()


def current_plan(conn, rid: int, days: int = 14) -> dict:
    today = date.today()
    out = rows(conn.execute(
        "SELECT w.date, w.description, w.target, w.status FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
        " WHERE w.runner_id=? AND p.active=1 AND w.date>=? AND w.date<=? ORDER BY w.date",
        (rid, (today - timedelta(days=3)).isoformat(), (today + timedelta(days=days)).isoformat())))
    for w in out:
        w["target"] = json.loads(w["target"] or "{}")
    return {"workouts": out}


def create_approval(conn, rid: int, reason: str, proposal: str) -> str:
    aid = secrets.token_urlsafe(9)
    conn.execute("INSERT INTO approvals (id, runner_id, reason, proposal) VALUES (?,?,?,?)",
                 (aid, rid, reason[:500], proposal[:2000]))
    conn.commit()
    return aid


def create_alert(conn, rid: int, type_: str, message: str) -> int:
    cur = conn.execute("INSERT INTO coach_alerts (runner_id, type, message) VALUES (?,?,?)",
                       (rid, type_, message[:1000]))
    conn.commit()
    return cur.lastrowid


def decide_approval(conn, approval_id: str, decision: str, note: str | None = None):
    """Coach decision, accepted only against an existing pending request. Returns the row or None."""
    if decision not in ("approved", "rejected", "edited"):
        raise ValueError("decision must be approved, rejected or edited")
    row = conn.execute("SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,)).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE approvals SET status=?, coach_note=?, decided_at=datetime('now') WHERE id=?",
                 (decision, note, approval_id))
    conn.commit()
    return dict(row) | {"status": decision, "coach_note": note}


def log_message(conn, rid: int, direction: str, type_: str, text: str, basis: dict | None = None) -> bool:
    """Save a message or report. The same outgoing text within the last hour is saved once
    (a scheduled job and Sarah may both record the same report). Returns True if saved."""
    text = (text or "").strip()
    if not text:
        return False
    if conn.execute("SELECT 1 FROM messages WHERE runner_id=? AND direction=? AND text=?"
                    " AND date>=datetime('now','-1 hour')", (rid, direction, text)).fetchone():
        return False
    conn.execute("INSERT INTO messages (runner_id, direction, type, text, basis) VALUES (?,?,?,?,?)",
                 (rid, direction, type_, text[:8000], json.dumps(basis) if basis else None))
    conn.commit()
    return True
