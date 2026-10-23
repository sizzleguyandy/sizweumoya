"""Each runner's complete record: one place to read anything, and one .db file per runner.

runners.db stays the single source of truth (every write goes there). After each write the
runner's own file, ``<records_dir>/<profile>.db``, is rebuilt from it, so it always holds
everything for that runner: profile, runs and per-km splits, strength sessions and sets,
reports and messages, notes, races, plans, fitness checks, alerts and approvals.
Secrets (MCP token hash, Hermes API key) are never copied into it.
"""
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from . import trends
from .config import settings
from .db import SCHEMA, rows, session

log = logging.getLogger(__name__)

# table -> how its rows belong to a runner (all carry runner_id).
TABLES = ("runners", "runs", "run_splits", "strength_sessions", "strength_sets", "messages",
          "weekly_summary", "fitness_checks", "tips_given", "coach_notes", "runner_summary",
          "races", "plans", "planned_workouts", "approvals", "coach_alerts", "wellness")
SECRET_COLUMNS = {"runners": {"mcp_token_hash", "hermes_api_key"}}

# What get_record can return. Each: (SQL after FROM/WHERE runner_id, date column, default order).
SECTIONS = {
    "runs": ("SELECT id, date, name, note, run_type, treadmill, distance_km, duration_s, avg_pace_s_km,"
             " avg_hr, max_hr, z1_s, z2_s, z3_s, z4_s, z5_s, elevation_m, temp_c, hr_drift_pct, source"
             " FROM runs WHERE runner_id=?", "date", "date DESC"),
    "splits": ("SELECT r.date, s.km, s.distance_m, s.duration_s, s.pace_s_km, s.avg_hr, s.max_hr"
               " FROM run_splits s JOIN runs r ON r.id=s.run_id WHERE s.runner_id=?", "r.date", "r.date DESC, s.km"),
    "strength": ("SELECT s.date, s.title, s.note, t.exercise, t.set_no, t.reps, t.weight_kg, t.duration_s"
                 " FROM strength_sessions s LEFT JOIN strength_sets t ON t.session_id=s.id WHERE s.runner_id=?",
                 "s.date", "s.date DESC, t.id"),
    "reports": ("SELECT date, type, text, basis FROM messages WHERE runner_id=? AND direction='out'",
                "date", "date DESC"),
    "messages": ("SELECT date, direction, type, text FROM messages WHERE runner_id=?", "date", "date DESC"),
    "notes": ("SELECT id, date, category, note, status, review_by FROM coach_notes WHERE runner_id=?",
              "date", "date DESC"),
    "races": ("SELECT name, date, distance_km, ascent_m, note FROM races WHERE runner_id=?", "date", "date"),
    "plan": ("SELECT w.date, w.description, w.target, w.status FROM planned_workouts w JOIN plans p"
             " ON p.id=w.plan_id WHERE w.runner_id=? AND p.active=1", "w.date", "w.date"),
    "weekly": ("SELECT * FROM weekly_summary WHERE runner_id=?", "week_start", "week_start DESC"),
    "fitness": ("SELECT date, ref_hr, pace_s_km, prev_pace_s_km, change_pct, verdict FROM fitness_checks"
                " WHERE runner_id=?", "date", "date DESC"),
    "tips": ("SELECT date, rule, text FROM tips_given WHERE runner_id=?", "date", "date DESC"),
}


# Ready-made views: the same SQL serves the person's own file (one person, no filter) and
# get_record on runners.db (filtered to the person). {w} is the runner filter, {a} its "AND" form.
VIEWS = {
    "weekly_km": """SELECT date(date, 'weekday 0', '-6 days') AS week_start, ROUND(SUM(distance_km), 1) AS km,
        COUNT(*) AS runs, ROUND(SUM(duration_s) / 60.0) AS minutes, ROUND(MAX(distance_km), 1) AS longest_km
        FROM runs {w} GROUP BY 1 ORDER BY 1 DESC""",
    "monthly_km": """SELECT substr(date, 1, 7) AS month, ROUND(SUM(distance_km), 1) AS km, COUNT(*) AS runs,
        ROUND(SUM(duration_s) / 60.0) AS minutes, ROUND(MAX(distance_km), 1) AS longest_km
        FROM runs {w} GROUP BY 1 ORDER BY 1 DESC""",
    "personal_bests": """SELECT 'longest run' AS record, ROUND(distance_km, 2) AS value, 'km' AS unit, date
          FROM (SELECT * FROM runs {w} ORDER BY distance_km DESC LIMIT 1)
        UNION ALL SELECT 'fastest ' || d.label || '+ (avg pace)', (CAST(r.avg_pace_s_km AS INTEGER) / 60) || ':'
          || printf('%02d', CAST(r.avg_pace_s_km AS INTEGER) % 60), 'min/km', r.date
          FROM (SELECT 5 AS km, '5K' AS label UNION ALL SELECT 10, '10K' UNION ALL SELECT 21.1, 'half'
                UNION ALL SELECT 42.2, 'marathon') d
          JOIN runs r ON r.id = (SELECT id FROM runs WHERE distance_km >= d.km AND treadmill = 0
                                 AND avg_pace_s_km IS NOT NULL {a} ORDER BY avg_pace_s_km LIMIT 1)""",
    "lift_records": """SELECT t.exercise, MAX(t.weight_kg) AS best_kg,
        ROUND(MAX(t.weight_kg * (1 + COALESCE(t.reps, 1) / 30.0)), 1) AS est_1rm_kg,
        COUNT(*) AS sets_logged, MAX(s.date) AS last_done
        FROM strength_sets t JOIN strength_sessions s ON s.id = t.session_id
        WHERE t.weight_kg > 0 {a2} GROUP BY t.exercise ORDER BY t.exercise""",
}


def _view_sql(name: str, filtered: bool) -> str:
    return VIEWS[name].format(w="WHERE runner_id=?" if filtered else "",
                              a="AND runner_id=?" if filtered else "",
                              a2="AND t.runner_id=?" if filtered else "")


def insights(conn, rid: int) -> dict:
    """The latest worked-out picture: progress, load, taper and strength records."""
    from datetime import date as _d
    from . import plans, queries
    m = plans.mode(conn, rid)
    out = {"focus": m["focus"], "coaching": {"running": m["running"], "strength": m["strength"]}}
    if m["focus"] in ("running", "hybrid"):
        out["progress"] = trends.progress(conn, rid)
        out["load_this_week"] = trends.load(conn, rid, queries.week_start(_d.today()))
        out["taper"] = trends.taper(conn, rid)
    if m["focus"] in ("strength", "hybrid"):
        out["strength_records"] = trends.strength_records(conn, rid)
    return out


def path_for(profile: str) -> Path:
    return Path(settings.records_dir) / f"{profile}.db"


def get_record(conn, rid: int, section: str, since: str = "", until: str = "", limit: int = 50) -> dict:
    """Read one section of the runner's record, optionally between two dates (YYYY-MM-DD)."""
    if section == "summary":
        return summary(conn, rid)
    if section == "insights":
        return {"section": section, **insights(conn, rid)}
    if section in VIEWS:
        sql = _view_sql(section, True)
        args = [rid] * sql.count("?")
        items = rows(conn.execute(sql, args))[:max(1, min(int(limit or 50), 500))]
        return {"section": section, "count": len(items), "items": items}
    if section not in SECTIONS:
        return {"error": "section must be one of: summary, insights, "
                         f"{', '.join(VIEWS)}, {', '.join(SECTIONS)}"}
    sql, col, order = SECTIONS[section]
    args: list = [rid]
    for value, op in ((since, ">="), (until, "<=")):
        if value:
            try:
                date.fromisoformat(value[:10])
            except ValueError:
                return {"error": "dates must be YYYY-MM-DD"}
            sql += f" AND {col}{op}?"
            args.append(value if op == ">=" else value + "~")  # '~' sorts after any time part
    sql += f" ORDER BY {order} LIMIT ?"
    args.append(max(1, min(int(limit or 50), 500)))
    items = rows(conn.execute(sql, args))
    for it in items:
        for k in ("target", "basis"):
            if isinstance(it.get(k), str) and it[k][:1] in "{[":
                it[k] = json.loads(it[k])
    return {"section": section, "count": len(items), "items": items}


def summary(conn, rid: int) -> dict:
    """What the record holds: counts and date ranges per section."""
    out = {}
    for name, table, col in (("runs", "runs", "date"), ("strength_sessions", "strength_sessions", "date"),
                             ("reports", "messages", "date"), ("notes", "coach_notes", "date"),
                             ("races", "races", "date"), ("fitness_checks", "fitness_checks", "date")):
        r = conn.execute(f"SELECT COUNT(*) n, MIN({col}) a, MAX({col}) b FROM {table} WHERE runner_id=?",
                         (rid,)).fetchone()
        out[name] = {"count": r["n"], "first": r["a"], "last": r["b"]}
    out["total_km"] = round(conn.execute("SELECT COALESCE(SUM(distance_km),0) FROM runs WHERE runner_id=?",
                                         (rid,)).fetchone()[0], 1)
    return out


def _schema_without_fk() -> str:
    """The tables only (indexes and foreign keys are not needed in a one-runner copy)."""
    text = SCHEMA.read_text().partition("CREATE INDEX")[0]
    # Secrets are left out of the copy, so their columns must accept NULL there, and tables
    # that only hold connection secrets are not created at all.
    text = re.sub(r"(--[^\n]*\n)*CREATE TABLE IF NOT EXISTS (hevy|garmin|intervals)_links \(.*?\);\n", "", text, flags=re.S)
    return text.replace("mcp_token_hash    TEXT NOT NULL", "mcp_token_hash    TEXT")


def export_runner(conn, rid: int, dest: Path | None = None) -> Path | None:
    """Rebuild the runner's own .db file from runners.db. Returns its path."""
    r = conn.execute("SELECT hermes_profile FROM runners WHERE runner_id=?", (rid,)).fetchone()
    if r is None:
        return None
    dest = Path(dest) if dest else path_for(r["hermes_profile"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(tmp)
    try:
        out.executescript(_schema_without_fk())
        for table in TABLES:
            src_cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
            dst_cols = {c[1] for c in out.execute(f"PRAGMA table_info({table})")}
            cols = [c for c in src_cols if c in dst_cols and c not in SECRET_COLUMNS.get(table, ())]
            if "runner_id" not in cols:  # table missing from an older database: nothing to copy
                continue
            data = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE runner_id=?", (rid,)).fetchall()
            if data:
                out.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [tuple(row) for row in data])
        for name in VIEWS:
            out.execute(f"CREATE VIEW {name} AS {_view_sql(name, False)}")
        out.execute("CREATE TABLE latest_insights (computed_at TEXT, section TEXT, data TEXT)")
        try:
            now = datetime.now().isoformat(timespec="seconds")
            out.executemany("INSERT INTO latest_insights VALUES (?,?,?)",
                            [(now, k, json.dumps(v, default=str)) for k, v in insights(conn, rid).items()])
        except Exception:
            log.exception("insights for runner %s could not be worked out", rid)
        out.execute("CREATE TABLE record_info (exported_at TEXT, source TEXT)")
        out.execute("INSERT INTO record_info VALUES (?, ?)",
                    (datetime.now().isoformat(timespec="seconds"), "runners.db"))
        out.commit()
    finally:
        out.close()
    os.replace(tmp, dest)
    return dest


def delete_file(profile: str) -> None:
    p = path_for(profile)
    if p.exists():
        p.unlink()


# ---- rebuilding in the background ------------------------------------------------
# A rebuild copies every table, so tool calls never wait for it: they ask for one and return.
# Requests for the same runner while a rebuild is running are folded into one more rebuild.

_lock = threading.Lock()
_running: set[int] = set()
_again: set[int] = set()


def schedule_export(rid: int) -> None:
    with _lock:
        if rid in _running:
            _again.add(rid)
            return
        _running.add(rid)
    threading.Thread(target=_export_loop, args=(rid,), name=f"record-{rid}", daemon=True).start()


def _export_loop(rid: int) -> None:
    while True:
        try:
            with session() as conn:
                export_runner(conn, rid)
        except Exception:
            log.exception("record file for runner %s could not be rebuilt", rid)
        with _lock:
            if rid in _again:
                _again.discard(rid)
                continue
            _running.discard(rid)
            return
