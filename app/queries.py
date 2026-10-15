"""Read/write helpers. Every function takes runner_id from the caller's resolved identity."""
import secrets
from datetime import date, timedelta

from . import stats
from .db import rows

NOTE_CATEGORIES = {"pattern", "preference", "health", "life", "motivation", "milestone"}


def profile(conn, rid: int) -> dict:
    r = conn.execute(
        "SELECT name, max_hr, resting_hr, hr_zones, goal, units, tier, joined FROM runners WHERE runner_id=?",
        (rid,),
    ).fetchone()
    return dict(r) if r else {}


def recent_runs(conn, rid: int, n: int = 5) -> list[dict]:
    out = rows(conn.execute(
        "SELECT date, name, distance_km, duration_s, avg_pace_s_km, avg_hr, z1_s, z2_s, z3_s, z4_s, z5_s,"
        " hr_drift_pct, run_type FROM runs WHERE runner_id=? ORDER BY date DESC LIMIT ?",
        (rid, max(1, min(n, 30))),
    ))
    for r in out:
        r["pace"] = stats.format_pace(r["avg_pace_s_km"])
        r["tips"] = stats.run_tips(r)
    return out


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


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
    conn.execute(
        "INSERT INTO weekly_summary (runner_id, week_start, total_km, runs, strength_sessions, easy_pct, km_change_pct)"
        " VALUES (?,?,?,?,?,?,?) ON CONFLICT(runner_id, week_start) DO UPDATE SET total_km=excluded.total_km,"
        " runs=excluded.runs, strength_sessions=excluded.strength_sessions, easy_pct=excluded.easy_pct,"
        " km_change_pct=excluded.km_change_pct",
        (rid, result["week_start"], result["total_km"], n, strength, result["easy_pct"], change),
    )
    conn.commit()
    return result


def fitness_trend(conn, rid: int, today: date | None = None) -> dict:
    today = today or date.today()
    w1, w2 = today - timedelta(days=28), today - timedelta(days=56)
    q = ("SELECT avg_pace_s_km, avg_hr FROM runs WHERE runner_id=? AND is_easy_valid=1"
         " AND avg_hr IS NOT NULL AND date>? AND date<=?")
    recent = rows(conn.execute(q, (rid, w1.isoformat(), today.isoformat())))
    previous = rows(conn.execute(q, (rid, w2.isoformat(), w1.isoformat())))
    res = stats.fitness_check(recent, previous)
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
    return out


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
    return {"workouts": rows(conn.execute(
        "SELECT w.date, w.description, w.target, w.status FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
        " WHERE w.runner_id=? AND p.active=1 AND w.date>=? AND w.date<=? ORDER BY w.date",
        (rid, (today - timedelta(days=3)).isoformat(), (today + timedelta(days=days)).isoformat())))}


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


def log_message(conn, rid: int, direction: str, type_: str, text: str) -> None:
    conn.execute("INSERT INTO messages (runner_id, direction, type, text) VALUES (?,?,?,?)",
                 (rid, direction, type_, text))
    conn.commit()
