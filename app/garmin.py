"""Garmin Connect (Starter access): activity files arrive by push.

Garmin POSTs {"activityFiles": [{userId, summaryId, fileType, callbackURL, activityType,
deviceName, startTimeInSeconds, activityId, manual}, ...]} and needs 200 within 30 seconds.
We answer at once; in the background each FIT file is downloaded once from its callbackURL
(valid 24 hours, a second download is refused) and stored through the same pipeline as a
.fit the person sends, so duplicates, splits, plan matching and the record all apply.

Every activity keeps the Garmin device name, for the "Garmin [device model]" attribution.
"""
import hashlib
import json
import logging
from urllib.parse import urlparse

import httpx

from . import upload
from .config import settings

log = logging.getLogger(__name__)

ALLOWED_HOSTS = {"apis.garmin.com"}          # only ever download from Garmin
MAX_FILE = 25 * 1024 * 1024
RUN_TYPES_PREFIX = ("RUNNING", "INDOOR_RUNNING", "STREET_RUNNING", "TRACK_RUNNING", "TRAIL_RUNNING",
                    "TREADMILL_RUNNING", "ULTRA_RUN", "VIRTUAL_RUN", "OBSTACLE_RUN")
STRENGTH_TYPES = ("STRENGTH_TRAINING",)


class GarminError(RuntimeError):
    pass


def link(conn, rid: int, garmin_user_id: str) -> None:
    conn.execute("INSERT INTO garmin_links (runner_id, garmin_user_id) VALUES (?,?) ON CONFLICT(runner_id)"
                 " DO UPDATE SET garmin_user_id=excluded.garmin_user_id, connected_at=datetime('now')",
                 (rid, garmin_user_id.strip()))
    conn.commit()


def unlink_user(conn, garmin_user_id: str) -> int | None:
    r = conn.execute("SELECT runner_id FROM garmin_links WHERE garmin_user_id=?", (garmin_user_id,)).fetchone()
    if r:
        conn.execute("DELETE FROM garmin_links WHERE runner_id=?", (r[0],))
        conn.commit()
        return r[0]
    return None


def runner_for_user(conn, garmin_user_id: str):
    return conn.execute("SELECT r.* FROM garmin_links g JOIN runners r ON r.runner_id=g.runner_id"
                        " WHERE g.garmin_user_id=? AND r.active=1", (garmin_user_id,)).fetchone()


def client_ok(headers) -> bool:
    """Pushes carry our client ID; anything else is not from Garmin for this app."""
    want = settings.garmin_client_id
    return bool(want) and headers.get("garmin-client-id", "") == want


def wanted(item: dict) -> bool:
    t = (item.get("activityType") or "").upper()
    return (t in RUN_TYPES_PREFIX or t in STRENGTH_TYPES) and (item.get("fileType") or "").upper() == "FIT"


def safe_url(url: str) -> bool:
    u = urlparse(url or "")
    return u.scheme == "https" and u.hostname in ALLOWED_HOSTS


def download(url: str) -> bytes:
    if not safe_url(url):
        raise GarminError("callback URL is not a Garmin address")
    with httpx.stream("GET", url, timeout=60, follow_redirects=False) as r:
        if r.status_code == 410:
            raise GarminError("already downloaded (410)")
        if r.status_code != 200:
            raise GarminError(f"Garmin answered {r.status_code}")
        data = b""
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > MAX_FILE:
                raise GarminError("file too large")
    return data


def store_item(conn, runner, item: dict) -> dict:
    """Download one activity file and store it. Returns process_blob's result plus the device."""
    data = download(item["callbackURL"])
    name = f"garmin-{item.get('summaryId') or item.get('activityId')}.fit"
    res = upload.process_blob(conn, runner, name, data, note="")
    key = "fit:" + hashlib.sha256(data).hexdigest()
    device = (item.get("deviceName") or "").strip() or None
    if res.get("status") == "stored":
        table = "runs" if res["kind"] == "run" else "strength_sessions"
        conn.execute(f"UPDATE {table} SET source='garmin', device=? WHERE activity_key=?", (device, key))
        conn.commit()
    res["device"] = device
    return res


def attribution(device: str | None) -> str:
    return f"Garmin {device}" if device and device.lower() != "unknown" else "Garmin"


def permissions_changed(conn, payload: dict) -> list[int]:
    """Record permission changes; a user who removed all permissions is unlinked."""
    affected = []
    for item in payload.get("userPermissionsChange") or []:
        uid, perms = item.get("userId"), item.get("permissions") or []
        r = conn.execute("SELECT runner_id FROM garmin_links WHERE garmin_user_id=?", (uid,)).fetchone()
        if not r:
            continue
        if perms:
            conn.execute("UPDATE garmin_links SET permissions=? WHERE runner_id=?", (json.dumps(perms), r[0]))
        else:
            conn.execute("DELETE FROM garmin_links WHERE runner_id=?", (r[0],))
        affected.append(r[0])
    conn.commit()
    return affected
