"""Turn a file the runner sent to the bot into stored activities.

Hermes saves Telegram attachments to disk and gives the agent a local path. The agent passes
that path to the `submit_activity_file` MCP tool, which calls `process_path` here.

Safety:
- The path must resolve inside this runner's own upload folder, built from the profile name
  the server authenticated (never from anything the model says). A runner can't make the
  server read another runner's files or anything else on the PC.
- Only .fit and .zip, with size limits; zips are read in memory, never extracted to disk.
- Every file is identified by its SHA-256, so the same file sent twice is stored once.
"""
import hashlib
import io
import logging
import os
import zipfile
from pathlib import Path

from . import ingest
from .config import settings
from .fitfile import FitError, parse

log = logging.getLogger(__name__)

MAX_FILE = 25 * 1024 * 1024
MAX_ZIP_MEMBERS = 300
MAX_ZIP_TOTAL = 300 * 1024 * 1024


class UploadRejected(ValueError):
    """The file can't be accepted (wrong place, type or size). Message is safe to show."""


def upload_root(profile: str) -> Path:
    """The only folder this profile's uploads may come from."""
    template = settings.upload_root_template
    if "{hermes_home}" in template and not settings.hermes_home:
        raise UploadRejected("file uploads aren't set up yet; please tell Coach Andy")
    root = template.format(hermes_home=settings.hermes_home, profile=profile)
    return Path(root).resolve()


def resolve(path: str, profile: str) -> Path:
    if not path or "\x00" in path:
        raise UploadRejected("no file path")
    root = upload_root(profile)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    try:
        real = p.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise UploadRejected("I couldn't find that file. Could you send it again?") from None
    if os.path.commonpath([str(real), str(root)]) != str(root):
        raise UploadRejected("that file isn't in your upload folder")
    if not real.is_file():
        raise UploadRejected("that isn't a file")
    if real.suffix.lower() not in (".fit", ".zip"):
        raise UploadRejected("please send a .fit file, or the .zip Garmin gives you")
    if real.stat().st_size > MAX_FILE:
        raise UploadRejected("that file is too large (25 MB max)")
    return real


def _fit_blobs(real: Path) -> list[tuple[str, bytes]]:
    data = real.read_bytes()
    if real.suffix.lower() == ".fit":
        return [(real.name, data)]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadRejected("that .zip file is damaged") from None
    members = [m for m in zf.infolist() if m.filename.lower().endswith(".fit") and not m.is_dir()]
    if not members:
        raise UploadRejected("there's no .fit file inside that .zip")
    if len(members) > MAX_ZIP_MEMBERS:
        raise UploadRejected(f"that .zip has more than {MAX_ZIP_MEMBERS} activities; send it in parts")
    if sum(m.file_size for m in members) > MAX_ZIP_TOTAL or any(m.file_size > MAX_FILE for m in members):
        raise UploadRejected("that .zip is too large once unpacked")
    return [(Path(m.filename).name, zf.read(m)) for m in members]


def process_blob(conn, runner, name: str, data: bytes, note: str = "") -> dict:
    key = "fit:" + hashlib.sha256(data).hexdigest()
    already = ingest.exists(conn, key)
    if already:
        return {"file": name, "status": "duplicate", "kind": already}
    try:
        act = parse(data)
    except FitError as e:
        return {"file": name, "status": "unreadable", "reason": str(e)}
    if act.local_date is None:
        return {"file": name, "status": "unreadable", "reason": "no start time"}
    ingest.update_max_hr(conn, runner, act.max_hr)
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (runner["runner_id"],)).fetchone()
    if act.kind == "run":
        row = ingest.store_run(conn, runner, act, key, note)
        return {"file": name, "status": "stored", "kind": "run", "date": row["date"],
                "distance_km": row["distance_km"], "duration_s": row["duration_s"],
                "run_type": row["run_type"], "treadmill": bool(row["treadmill"]),
                "short": ingest.is_short(row["distance_km"], row["duration_s"]),
                "plan": row["plan"], "replaced_sheet_row": row["replaced_sheet_row"]}
    if act.kind == "strength":
        res = ingest.store_strength_fit(conn, runner, act, key, note)
        return {"file": name, "status": "stored", "kind": "strength", "date": act.local_date,
                "sets": res["sets"], "duration_s": int(act.timer_s or act.elapsed_s), "plan": res["plan"]}
    return {"file": name, "status": "skipped", "kind": act.sport,
            "reason": "only runs and strength sessions are tracked"}


def process_path(conn, runner, path: str, note: str = "") -> dict:
    """Validate, read and store every activity in the file. Returns a summary for the agent."""
    real = resolve(path, runner["hermes_profile"])
    blobs = _fit_blobs(real)
    results = [process_blob(conn, runner, name, data, note if len(blobs) == 1 else "") for name, data in blobs]
    stored = [r for r in results if r["status"] == "stored"]
    summary = {
        "files": len(results),
        "stored_runs": sum(1 for r in stored if r["kind"] == "run"),
        "stored_strength": sum(1 for r in stored if r["kind"] == "strength"),
        "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
        "skipped": sum(1 for r in results if r["status"] in ("skipped", "unreadable")),
    }
    if stored:
        dates = sorted(r["date"] for r in stored)
        summary["date_range"] = [dates[0], dates[-1]]
    # One file: give the agent the detail for a debrief. Many files: it's a history import.
    if len(results) == 1:
        summary["activity"] = results[0]
    else:
        summary["history_import"] = True
    return summary
