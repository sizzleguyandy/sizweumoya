"""SQLite access for runners.db."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA = Path(__file__).with_name("schema.sql")

# Columns the file-upload version needs on tables created by the earlier Strava version.
_REBUILD = {
    "runs": "strava_id",
    "strength_sessions": "strava_id",
}


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or settings.db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _table_sql(table: str) -> str:
    text = SCHEMA.read_text()
    start = text.index(f"CREATE TABLE IF NOT EXISTS {table} (")
    return text[start:text.index(");", start) + 2]


def _migrate(conn) -> None:
    """Bring a database created by the Strava version up to the file-upload schema.

    runs / strength_sessions: strava_id -> activity_key ('strava:<id>' for old rows).
    runners: add zones_locked. Old strava_* columns on runners are left in place, unused.
    """
    for table, old_col in _REBUILD.items():
        cols = _columns(conn, table)
        if not cols or old_col not in cols:
            continue
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        new_sql = _table_sql(table).replace(f"CREATE TABLE IF NOT EXISTS {table} (",
                                            f"CREATE TABLE {table}_new (")
        conn.execute(new_sql)
        new_cols = _columns(conn, f"{table}_new")
        shared = [c for c in new_cols if c in cols]
        select = [("'strava:' || strava_id" if c == "activity_key" else c) for c in shared]
        if "activity_key" not in shared:
            shared.append("activity_key")
            select.append("'strava:' || strava_id")
        if "source" not in cols:
            shared.append("source")
            select.append("'strava'")
        conn.execute(f"INSERT INTO {table}_new ({', '.join(shared)}) SELECT {', '.join(select)} FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        conn.commit()  # PRAGMA foreign_keys is ignored inside an open transaction
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
    if "zones_locked" not in _columns(conn, "runners"):
        conn.execute("ALTER TABLE runners ADD COLUMN zones_locked INTEGER NOT NULL DEFAULT 0")
    for table, col, decl in (("runners", "easy_hr_cap", "INTEGER"), ("runners", "weight_kg", "REAL"),
                             ("runners", "watch", "TEXT"),
                             ("runners", "focus", "TEXT NOT NULL DEFAULT 'running'"), ("runners", "consent_at", "TEXT"),
                             ("runners", "onboarded_at", "TEXT"), ("messages", "basis", "TEXT"),
                             ("approvals", "kind", "TEXT NOT NULL DEFAULT 'plan'"),
                             ("approvals", "payload", "TEXT"),
                             ("weekly_summary", "flag_level", "TEXT"), ("weekly_summary", "flags", "TEXT"),
                             ("runs", "device", "TEXT"), ("strength_sessions", "device", "TEXT")):
        if col not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            if col == "onboarded_at":
                # Runners from before onboarding existed are already live.
                conn.execute("UPDATE runners SET onboarded_at=joined")
    if "completed_strava_id" in _columns(conn, "planned_workouts"):
        conn.execute("ALTER TABLE planned_workouts RENAME COLUMN completed_strava_id TO completed_activity_key")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    text = SCHEMA.read_text()
    tables, _, indexes = text.partition("CREATE INDEX")
    conn.executescript(tables)
    _migrate(conn)
    conn.executescript("CREATE INDEX" + indexes)
    conn.commit()


@contextmanager
def session(path: str | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
