"""People who message the bot before they are registered.

Hermes turns unknown people away itself (unauthorized_dm_behavior), so this service never sees
their message. It does see the gateway's log: a scheduled job reads the new lines, finds the
chat IDs Hermes declined, and tells Coach Andy once per person. He replies "add <name> <id>"
in the coach chat (coach_add_runner); the Hermes side (profile, route, allowlist, restart) is
then one sentence to the PC agent, because it needs the PC.

The log line format is Hermes's, so the pattern is a setting: JOIN_LOG_PATTERN, a regex with a
named group `chat_id` (and optionally `name`). JOIN_LOG_FILE is the gateway log to read.
"""
import os
import re
from pathlib import Path

DEFAULT_PATTERN = (r"(?i)unauthori[sz]ed.*?(?:chat|user|sender)[ _]?(?:id)?['\"=: ]+(?P<chat_id>\d{5,15})"
                   r"(?:.*?(?:username|name)['\"=: ]+@?(?P<name>[\w.-]{2,40}))?")
STATE_KEY = "join_log_offset"


def log_file() -> Path | None:
    p = os.environ.get("JOIN_LOG_FILE", "").strip()
    return Path(p) if p else None


def pattern() -> re.Pattern:
    return re.compile(os.environ.get("JOIN_LOG_PATTERN", "").strip() or DEFAULT_PATTERN)


def find(text: str) -> list[tuple[str, str | None]]:
    """(chat_id, name) for each declined message in a piece of log, in order, without repeats."""
    seen, out = set(), []
    for m in pattern().finditer(text):
        cid = m.group("chat_id")
        if cid not in seen:
            seen.add(cid)
            out.append((cid, (m.groupdict().get("name") or "").strip() or None))
    return out


def scan(conn, path: Path | None = None) -> list[dict]:
    """Read what's new in the gateway log and return the people not seen before (and not
    registered). Remembers how far it read; starts again from the top if the log was rotated."""
    path = path or log_file()
    if path is None or not path.exists():
        return []
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (STATE_KEY,)).fetchone()
    offset = int(row["value"]) if row else 0
    size = path.stat().st_size
    if offset > size:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        text = f.read().decode("utf-8", "replace")
    conn.execute("INSERT INTO app_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (STATE_KEY, str(size)))
    new = []
    for cid, name in find(text):
        if conn.execute("SELECT 1 FROM runners WHERE telegram_chat_id=?", (cid,)).fetchone():
            continue
        cur = conn.execute("INSERT OR IGNORE INTO join_requests (chat_id, name) VALUES (?,?)", (cid, name))
        if cur.rowcount:
            new.append({"chat_id": cid, "name": name})
    conn.commit()
    return new


def pending(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT chat_id, name, first_seen FROM join_requests WHERE status='new' ORDER BY first_seen")]


def close(conn, chat_id: str, status: str) -> None:
    conn.execute("UPDATE join_requests SET status=? WHERE chat_id=?", (status, str(chat_id)))
    conn.commit()
