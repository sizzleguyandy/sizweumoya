"""New-runner onboarding: consent, watch, history, zones, races and injuries, then go live.

Sarah asks for what is missing, in order; `status` tells her what that is. Nothing here
writes a stat the coach owns: zones from a screenshot become a request Coach Andy approves.
"""
import json
from datetime import date, datetime

from . import queries

WATCHES = {
    "garmin": (
        "On a computer, open connect.garmin.com, open the run, click the gear icon (top right) and "
        "choose \"Export Original\". That downloads a .zip with the .fit file inside; send me the .zip "
        "as it is. For your history, you can select several runs that way, or request your full data "
        "export under Account settings > Data management (Garmin emails it, which can take a few days)."),
    "coros": (
        "In the COROS app, open the run, tap the share or ... menu and choose to export the .fit file, "
        "then share it to me here."),
    "suunto": (
        "In the Suunto app, open the workout, tap the ... menu and look for an export or share option "
        "that gives a .fit file, then share it to me here."),
    "polar": (
        "On a computer, open flow.polar.com, open the training session and use the export option. If "
        ".fit isn't offered, tell me which formats are, and I'll let Coach Andy know."),
    "zepp": (
        "In the Zepp app, open the workout, tap the ... or share menu and export the .fit file, then "
        "share it to me here."),
    "apple": (
        "Apple Watch doesn't make .fit files on its own. A small app such as HealthFit on your iPhone "
        "can export each workout as a .fit file that you then share to me here."),
}
OTHER_WATCH = ("Look in your watch app for the workout's export or share option and choose .fit. "
               "If only .gpx or .tcx is offered, tell me which, and I'll let Coach Andy know.")

STEPS = ("consent", "focus", "watch", "history", "zones", "races", "health", "finish")
FOCUSES = ("running", "strength", "hybrid")


def instructions(brand: str) -> dict:
    key = (brand or "").strip().lower()
    for k in WATCHES:
        if k in key or (k == "zepp" and "amazfit" in key) or (k == "apple" and "iphone" in key):
            return {"brand": k, "how_to_send_files": WATCHES[k]}
    return {"brand": key or "unknown", "how_to_send_files": OTHER_WATCH}


def status(conn, rid: int) -> dict:
    """What onboarding still needs, in the order Sarah should ask for it."""
    r = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    runs = conn.execute("SELECT COUNT(*) FROM runs WHERE runner_id=?", (rid,)).fetchone()[0]
    zones_req = conn.execute("SELECT status FROM approvals WHERE runner_id=? AND kind='zones'"
                             " ORDER BY created DESC LIMIT 1", (rid,)).fetchone()
    asked = {n["note"] for n in conn.execute(
        "SELECT note FROM coach_notes WHERE runner_id=? AND category='profile'", (rid,))}
    lifts = conn.execute("SELECT COUNT(*) FROM strength_sessions WHERE runner_id=?", (rid,)).fetchone()[0]
    focus_set = "focus set" in asked
    runner_steps = r["focus"] in ("running", "hybrid")  # watch, zones and races are for running
    done = {
        "consent": bool(r["consent_at"]),
        "focus": focus_set,
        "watch": bool(r["watch"]) or (focus_set and not runner_steps),
        "history": runs > 0 or lifts > 0 or "history: starting fresh" in asked,
        "zones": (not runner_steps and focus_set) or bool(r["zones_locked"]) or bool(zones_req)
                 or "zones: none available" in asked,
        "races": (not runner_steps and focus_set) or "races asked" in asked,
        "health": "health asked" in asked,
        "finish": bool(r["onboarded_at"]),
    }
    nxt = next((s for s in STEPS if not done[s]), None)
    return {"onboarded": done["finish"], "focus": r["focus"] if focus_set else None,
            "done": done, "next_step": nxt,
            "zones_request": zones_req["status"] if zones_req else None, "runs_on_file": runs}


def record_consent(conn, rid: int) -> str:
    conn.execute("UPDATE runners SET consent_at=? WHERE runner_id=?",
                 (datetime.now().isoformat(timespec="seconds"), rid))
    conn.commit()
    return "consent recorded"


def set_focus(conn, rid: int, focus: str) -> str:
    if focus not in FOCUSES:
        raise ValueError(f"focus must be one of {FOCUSES}")
    conn.execute("UPDATE runners SET focus=? WHERE runner_id=?", (focus, rid))
    conn.commit()
    if not conn.execute("SELECT 1 FROM coach_notes WHERE runner_id=? AND category='profile' AND note='focus set'",
                        (rid,)).fetchone():
        mark(conn, rid, "focus set")
    return focus


def set_watch(conn, rid: int, brand: str) -> dict:
    info = instructions(brand)
    conn.execute("UPDATE runners SET watch=? WHERE runner_id=?", (info["brand"], rid))
    conn.commit()
    return info


def mark(conn, rid: int, what: str) -> None:
    """Record that an onboarding question was asked (answers are saved by their own tools)."""
    conn.execute("INSERT INTO coach_notes (runner_id, source, category, note, status) VALUES (?,?,?,?,?)",
                 (rid, "onboarding", "profile", what, "active"))
    conn.commit()


def save_race(conn, rid: int, name: str, race_date: str, distance_km: float, note: str = "") -> dict:
    d = date.fromisoformat(race_date)
    if d < date.today():
        raise ValueError("that date has already passed")
    if not 1 <= float(distance_km) <= 250:
        raise ValueError("distance must be in km")
    cur = conn.execute("INSERT INTO races (runner_id, name, date, distance_km, note) VALUES (?,?,?,?,?)",
                       (rid, name.strip()[:120], d.isoformat(), float(distance_km), (note or "")[:500] or None))
    conn.commit()
    return {"race_id": cur.lastrowid, "days_to_go": (d - date.today()).days}


def propose_zones(conn, rid: int, zone_tops: list[int], max_hr: int | None, source: str) -> str:
    """Zones read from a screenshot: a request for Coach Andy, applied only on approval."""
    tops = [int(z) for z in zone_tops]
    if len(tops) != 4 or tops != sorted(tops) or not 80 <= tops[0] <= tops[-1] <= 220:
        raise ValueError("zone_tops must be the top of zones 1-4, rising, e.g. [132, 144, 152, 162]")
    if max_hr is not None and not tops[-1] < int(max_hr) <= 230:
        raise ValueError("max_hr must be above the top of zone 4")
    payload = {"zones": tops, "max_hr": int(max_hr) if max_hr else None, "source": source[:200]}
    aid = queries.create_approval(conn, rid, "Heart rate zones from the runner's watch",
                                  f"Set zones to {tops} (zone 5 above {tops[-1]})"
                                  + (f", max HR {max_hr}" if max_hr else "") + f". Source: {source[:200]}")
    conn.execute("UPDATE approvals SET kind='zones', payload=? WHERE id=?", (json.dumps(payload), aid))
    conn.commit()
    return aid


def apply_zones(conn, approval: dict) -> bool:
    """Called when Coach Andy approves a zones request."""
    if approval.get("kind") != "zones" or not approval.get("payload"):
        return False
    p = json.loads(approval["payload"])
    conn.execute("UPDATE runners SET hr_zones=?, zones_locked=1 WHERE runner_id=?",
                 (json.dumps(p["zones"]), approval["runner_id"]))
    if p.get("max_hr"):
        conn.execute("UPDATE runners SET max_hr=? WHERE runner_id=?", (p["max_hr"], approval["runner_id"]))
    conn.commit()
    return True


def finish(conn, rid: int) -> dict:
    """Mark onboarding done and build the new-runner summary for Coach Andy."""
    st = status(conn, rid)
    missing = [s for s, ok in st["done"].items() if not ok and s != "finish"]
    if "consent" in missing:
        raise ValueError("consent has not been given")
    r = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    conn.execute("UPDATE runners SET onboarded_at=? WHERE runner_id=?",
                 (datetime.now().isoformat(timespec="seconds"), rid))
    conn.commit()
    base = queries.baseline(conn, rid)
    races = [dict(x) for x in conn.execute(
        "SELECT name, date, distance_km FROM races WHERE runner_id=? AND date>=date('now') ORDER BY date", (rid,))]
    health = [x["note"] for x in conn.execute(
        "SELECT note FROM coach_notes WHERE runner_id=? AND category='health' AND status='active'", (rid,))]
    parts = [f"{r['name']} is set up: focus {r['focus']}, {r['watch'] or 'no watch given'}."]
    lifts = conn.execute("SELECT COUNT(*) FROM strength_sessions WHERE runner_id=?", (rid,)).fetchone()[0]
    if lifts:
        parts.append(f"{lifts} strength sessions on file.")
    if base.get("runs"):
        parts.append(f"{base['avg_km_per_week']} km a week over {base['runs']} runs, longest "
                     f"{base['longest_run_km']} km" + (f", {base['easy_pct']}% easy." if base.get("easy_pct") else "."))
    elif not lifts:
        parts.append("No history sent; starting fresh.")
    parts.append("Races: " + ("; ".join(f"{x['name']} {x['distance_km']} km on {x['date']}" for x in races)
                              if races else "none planned."))
    parts.append("Health: " + ("; ".join(health) if health else "nothing reported."))
    if st["zones_request"] == "pending":
        parts.append("Zones from their watch are waiting for your approval.")
    if missing:
        parts.append("Not completed: " + ", ".join(missing) + ".")
    return {"coach_summary": " ".join(parts), "missing": missing}
