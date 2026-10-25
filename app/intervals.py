"""Intervals.icu: push programme sessions to the calendar (and from there to the watch), pull
activities back, and read daily wellness.

Personal connection: athlete ID + API key, HTTP Basic auth with the username "API_KEY".
Coach Andy connects his own account that way (marked is_coach). Every other athlete links
through him: they accept Coach Andy as their coach in Intervals.icu, give Sarah their athlete
ID (not a secret), Coach Andy confirms it is them, and from then on their calendar and
activities are reached with his key. Nobody else's key is ever asked for or stored.

Pushed sessions carry external_id "sizwe-<planned_workout id>", so re-pushing updates them
instead of adding copies. A session with structured `steps` (Intervals.icu workout text) is
followed step by step on the watch; one without becomes a plain calendar note.
"""
import logging
import re
from datetime import date, timedelta

import httpx

from . import upload
from .config import settings

log = logging.getLogger(__name__)

RUN_KINDS = {"easy", "long", "workout", "benchmark", "race"}


class IntervalsError(RuntimeError):
    pass


# ---- connection --------------------------------------------------------------------

def connect(conn, rid: int, athlete_id: str, api_key: str) -> None:
    athlete_id, api_key = (athlete_id or "").strip(), (api_key or "").strip()
    if not athlete_id or len(api_key) < 10:
        raise IntervalsError("athlete ID and API key are both needed")
    conn.execute("INSERT INTO intervals_links (runner_id, athlete_id, api_key) VALUES (?,?,?) ON CONFLICT(runner_id)"
                 " DO UPDATE SET athlete_id=excluded.athlete_id, api_key=excluded.api_key, via_coach=0, status='active',"
                 " connected_at=datetime('now')",
                 (rid, athlete_id, api_key))
    conn.commit()


def link_for(conn, rid: int, *, any_status: bool = False) -> dict:
    """The person's link, with the key to use (Coach Andy's for an athlete he coaches)."""
    r = conn.execute("SELECT * FROM intervals_links WHERE runner_id=?", (rid,)).fetchone()
    if r is None:
        raise IntervalsError("this person isn't connected to Intervals.icu")
    if r["status"] != "active" and not any_status:
        raise IntervalsError(f"this person's Intervals.icu link is {r['status']}, not active yet")
    link = dict(r)
    if link["via_coach"]:
        link["api_key"] = coach_key(conn)
    return link


def coach_key(conn) -> str:
    r = conn.execute("SELECT api_key FROM intervals_links WHERE is_coach=1 AND api_key!='' LIMIT 1").fetchone()
    if r is None:
        raise IntervalsError("Coach Andy's own Intervals.icu account isn't marked as the coach account "
                             "(python -m app.admin intervals-set-coach --runner <his id>)")
    return r["api_key"]


def set_coach(conn, rid: int) -> None:
    r = conn.execute("SELECT api_key FROM intervals_links WHERE runner_id=?", (rid,)).fetchone()
    if r is None or not r["api_key"]:
        raise IntervalsError("connect this person with their own key first (intervals-connect)")
    conn.execute("UPDATE intervals_links SET is_coach=(runner_id=?)", (rid,))
    conn.commit()


# ---- linking an athlete through Coach Andy -------------------------------------------

ATHLETE_RE = re.compile(r"^i?\d{1,10}$")


def normalise_athlete_id(athlete_id: str) -> str:
    a = (athlete_id or "").strip().lower().replace(" ", "")
    if not ATHLETE_RE.match(a):
        raise IntervalsError("that doesn't look like an Intervals.icu athlete ID (it looks like i123456)")
    return a


def request_link(conn, rid: int, athlete_id: str) -> dict:
    """The person says this is their athlete ID. Saved as 'requested' until Coach Andy confirms."""
    a = normalise_athlete_id(athlete_id)
    other = conn.execute("SELECT runner_id FROM intervals_links WHERE athlete_id=? AND runner_id!=?",
                         (a, rid)).fetchone()
    if other:
        raise IntervalsError("that athlete ID is already linked to someone else; Coach Andy has been told")
    cur = conn.execute("SELECT status, via_coach FROM intervals_links WHERE runner_id=?", (rid,)).fetchone()
    if cur and not cur["via_coach"]:
        raise IntervalsError("this person is already connected with their own key")
    conn.execute("INSERT INTO intervals_links (runner_id, athlete_id, api_key, via_coach, status)"
                 " VALUES (?,?,'',1,'requested') ON CONFLICT(runner_id) DO UPDATE SET athlete_id=excluded.athlete_id,"
                 " status='requested', connected_at=datetime('now')", (rid, a))
    conn.commit()
    return {"athlete_id": a, "status": "requested"}


def approve_link(conn, rid: int) -> None:
    conn.execute("UPDATE intervals_links SET status='approved' WHERE runner_id=? AND status='requested'", (rid,))
    conn.commit()


def check_access(conn, rid: int) -> dict | None:
    """The athlete's settings if Coach Andy's key can read them (they accepted him as coach), else None."""
    link = link_for(conn, rid, any_status=True)
    with _client(link) as c:
        r = c.get(f"/athlete/{link['athlete_id']}")
    if r.status_code in (401, 403, 404):
        return None
    return _check(r).json()


def zones_from_athlete(athlete: dict) -> dict | None:
    """Run threshold HR and max HR from the athlete's Intervals.icu settings, as our four zone
    tops (80/87/92/98% of threshold, the split Coach Andy uses)."""
    for s in athlete.get("sportSettings") or []:
        if "Run" not in (s.get("types") or []):
            continue
        lthr, mx = s.get("lthr"), s.get("max_hr")
        if not lthr or not 120 <= int(lthr) <= 210:
            return None
        tops = [round(int(lthr) * f) for f in (0.80, 0.87, 0.92, 0.98)]
        return {"lthr": int(lthr), "max_hr": int(mx) if mx and int(mx) > tops[-1] else None, "zones": tops}
    return None


def activate(conn, rid: int, history_weeks: int = 8) -> dict:
    """First successful access: mark active, bring in recent history and wellness, and put any
    programme on their calendar."""
    conn.execute("UPDATE intervals_links SET status='active' WHERE runner_id=?", (rid,))
    conn.commit()
    pulled = pull_activities(conn, rid, date.today() - timedelta(weeks=history_weeks))
    out = {"activities": pulled["counts"]}
    try:
        out["wellness_days"] = pull_wellness(conn, rid, date.today() - timedelta(days=14))
        if planned_sessions(conn, rid, date.today(), date.today() + timedelta(days=28)):
            out["calendar"] = reconcile(conn, rid)
    except IntervalsError as e:
        out["warning"] = str(e)
    return out


def _client(link) -> httpx.Client:
    return httpx.Client(base_url=f"{settings.intervals_base_url}/api/v1", auth=("API_KEY", link["api_key"]),
                        timeout=60, headers={"accept": "application/json"})


def _check(r: httpx.Response):
    if r.status_code >= 400:
        raise IntervalsError(f"Intervals.icu answered {r.status_code}: {r.text[:200]}")
    return r


# ---- push: programme -> calendar -> watch --------------------------------------------

def _hr_zone_text(target: dict) -> str:
    return "Z2 HR" if target.get("kind") in ("easy", "long") else ""


def short_name(description: str, limit: int = 40) -> str:
    """The session's short title for the watch: the text before the first dash or full stop."""
    import re
    head = re.split(r"\s+[—–-]\s+|\.\s", description or "", maxsplit=1)[0].strip() or "Session"
    return head if len(head) <= limit else head[:limit - 1].rstrip() + "…"


def to_event(w, runner) -> dict:
    """One planned session as an Intervals.icu calendar event."""
    import json
    t = json.loads(w["target"] or "{}")
    is_run = t.get("kind") in RUN_KINDS or w["ptype"] == "run"
    lines = [w["description"]]
    if t.get("km"):
        lines.append(f"Distance: {t['km']:g} km")
    cap = t.get("hr_max") or (runner["easy_hr_cap"] if t.get("kind") in ("easy", "long") else None)
    if t.get("hr_range") and t.get("kind") == "long":
        # A long run with faster parts: easy running under the cap, the marked parts in the range.
        if cap:
            lines.append(f"Easy running: under {cap} bpm")
        lines.append(f"Faster parts: {t['hr_range'][0]}-{t['hr_range'][1]} bpm")
    elif t.get("hr_range"):
        lines.append(f"Heart rate: {t['hr_range'][0]}-{t['hr_range'][1]} bpm")
    elif cap:
        lines.append(f"Heart rate: under {cap} bpm")
    if t.get("exercises"):
        lines += [f"- {e}" for e in t["exercises"]]
    steps = t.get("steps")
    # Only all-easy runs get an automatic one-step workout; anything with faster parts needs
    # steps written by Coach Andy, otherwise the watch would coach the whole run as easy.
    if not steps and is_run and t.get("kind") in ("easy", "long") and t.get("km") and not t.get("hr_range"):
        steps = f"- {t['km']:g}km {_hr_zone_text(t)}".strip()   # a simple one-step run the watch can follow
    description = "\n".join(lines) + (f"\n\n{steps}" if steps else "")
    return {"category": "WORKOUT" if steps or is_run else "NOTE",
            "start_date_local": f"{w['date']}T00:00:00",
            "type": "Run" if is_run else "WeightTraining",
            "name": short_name(w["description"]),
            "description": description,
            "external_id": f"sizwe-{w['id']}"}


def planned_sessions(conn, rid: int, start: date, end: date) -> list:
    return conn.execute(
        "SELECT w.*, p.type ptype FROM planned_workouts w JOIN plans p ON p.id=w.plan_id WHERE w.runner_id=?"
        " AND p.active=1 AND w.date>=? AND w.date<=? AND w.status IN ('planned','missed') ORDER BY w.date",
        (rid, start.isoformat(), end.isoformat())).fetchall()


def push(conn, rid: int, start: date | None = None, days: int = 28, dry_run: bool = False) -> dict:
    start = start or date.today()
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    events = [to_event(w, runner) for w in planned_sessions(conn, rid, start, start + timedelta(days=days))]
    if dry_run or not events:
        return {"dry_run": dry_run, "events": events}
    link = link_for(conn, rid)
    with _client(link) as c:
        r = _check(c.post(f"/athlete/{link['athlete_id']}/events/bulk", params={"upsert": "true"}, json=events))
    return {"pushed": len(events), "response_items": len(r.json()) if r.content else 0}


EXT_PREFIX = "sizwe-"


def reconcile(conn, rid: int, days: int = 28) -> dict:
    """Make the Intervals.icu calendar match the programme for the next `days` days: send every
    open session (new, moved or edited ones are updated in place), and remove calendar entries we
    created whose session no longer exists or whose programme was replaced. Completed sessions and
    anything the person added themselves are never touched."""
    start, end = date.today(), date.today() + timedelta(days=days)
    sent = push(conn, rid, start=start, days=days)
    link = link_for(conn, rid)
    removed = 0
    with _client(link) as c:
        events = _check(c.get(f"/athlete/{link['athlete_id']}/events",
                              params={"oldest": start.isoformat(), "newest": end.isoformat()})).json()
        for e in events:
            ext = str(e.get("external_id") or "")
            if not ext.startswith(EXT_PREFIX) or not ext[len(EXT_PREFIX):].isdigit():
                continue
            w = conn.execute(
                "SELECT w.status, p.active FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
                " WHERE w.id=? AND w.runner_id=?", (int(ext[len(EXT_PREFIX):]), rid)).fetchone()
            if w is None or not w["active"]:
                _check(c.delete(f"/athlete/{link['athlete_id']}/events/{e['id']}"))
                removed += 1
    return {"sent": sent.get("pushed", 0), "removed": removed}


# ---- pull: activities and wellness ----------------------------------------------------

def from_strava(activity: dict) -> bool:
    """True for anything that came from Strava, by any marker Intervals.icu gives."""
    src = " ".join(str(activity.get(k) or "") for k in ("source", "external_id", "_note", "device_name")).upper()
    return "STRAVA" in src or bool(activity.get("strava_id"))


def pull_activities(conn, rid: int, since: date) -> dict:
    """Download new activities' original files and store them through the .fit pipeline."""
    link = link_for(conn, rid)
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    counts = {"stored": 0, "duplicate": 0, "skipped": 0, "unreadable": 0, "failed": 0}
    results = []
    with _client(link) as c:
        acts = _check(c.get(f"/athlete/{link['athlete_id']}/activities",
                            params={"oldest": since.isoformat(), "newest": date.today().isoformat()})).json()
        for a in acts:
            if from_strava(a):
                counts["skipped"] += 1   # nothing from Strava is used anywhere in this system
                continue
            try:
                f = c.get(f"/activity/{a['id']}/fit-file")
                if f.status_code == 404:
                    f = c.get(f"/activity/{a['id']}/file")
                _check(f)
                data = f.content
                if data[:2] == b"\x1f\x8b":   # gzip
                    import gzip
                    data = gzip.decompress(data)
                res = upload.process_blob(conn, runner, f"intervals-{a['id']}.fit", data)
                counts[res["status"] if res["status"] in counts else "skipped"] += 1
                if res["status"] == "stored":
                    results.append(res)
            except Exception as e:
                log.warning("intervals activity %s: %s", a.get("id"), e)
                counts["failed"] += 1
    conn.execute("UPDATE intervals_links SET last_sync=datetime('now') WHERE runner_id=?", (rid,))
    conn.commit()
    return {"counts": counts, "stored": results}


def pull_wellness(conn, rid: int, since: date) -> int:
    link = link_for(conn, rid)
    with _client(link) as c:
        days = _check(c.get(f"/athlete/{link['athlete_id']}/wellness",
                            params={"oldest": since.isoformat(), "newest": date.today().isoformat()})).json()
    n = 0
    for d in days:
        day = d.get("id") or d.get("date")
        if not day:
            continue
        conn.execute(
            "INSERT INTO wellness (runner_id, date, resting_hr, hrv, sleep_s, sleep_score, weight_kg) VALUES"
            " (?,?,?,?,?,?,?) ON CONFLICT(runner_id, date) DO UPDATE SET resting_hr=excluded.resting_hr,"
            " hrv=excluded.hrv, sleep_s=excluded.sleep_s, sleep_score=excluded.sleep_score, weight_kg=excluded.weight_kg",
            (rid, str(day)[:10], d.get("restingHR"), d.get("hrv"), d.get("sleepSecs"), d.get("sleepScore"),
             d.get("weight")))
        n += 1
    conn.commit()
    return n
