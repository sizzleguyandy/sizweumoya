"""New-runner onboarding: consent, watch, syncing, history, zones, races and injuries, then go live.

Sarah asks for what is missing, in order; `status` tells her what that is. Nothing here
writes a stat the coach owns: zones from a screenshot become a request Coach Andy approves.
"""
import json
from datetime import date, datetime

from . import intervals, queries

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

STEPS = ("consent", "focus", "watch", "sync", "history", "zones", "races", "health", "finish")
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
    link = conn.execute("SELECT athlete_id, status FROM intervals_links WHERE runner_id=?", (rid,)).fetchone()
    focus_set = "focus set" in asked
    runner_steps = r["focus"] in ("running", "hybrid")  # watch, zones and races are for running
    done = {
        "consent": bool(r["consent_at"]),
        "focus": focus_set,
        "watch": bool(r["watch"]) or (focus_set and not runner_steps),
        "sync": (focus_set and not runner_steps) or bool(link) or "sync: files instead" in asked,
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
            "zones_request": zones_req["status"] if zones_req else None, "runs_on_file": runs,
            "intervals": link["status"] if link else None}


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
    sync = sync_instructions(info["brand"])
    if sync:
        info["sync"] = sync   # offer this first; sending files stays as the fallback
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


# ---- syncing through Intervals.icu ---------------------------------------------------

SYNC_HOW = (
    "1) Make a free account at intervals.icu (sign up with email; don't connect Strava, we don't use it). "
    "2) In Intervals.icu go to Settings and connect your watch's own app ({app}). {push} "
    "3) Find your athlete ID: on intervals.icu click your profile picture or Settings; the ID starts "
    "with i, like i123456, and also shows in the page address. Send it to me here. Never send a "
    "password or API key, I don't need one. "
    "4) Coach Andy will send you a coaching request in Intervals.icu; accept it. After that your "
    "runs come to me on their own, and any programme goes onto your Intervals.icu calendar.")
SYNC_APPS = {
    "garmin": ("Garmin Connect", "Tick the option to upload planned workouts, so your sessions land on the watch."),
    "zepp": ("Zepp", "Your planned sessions can then go to the watch too."),
    "coros": ("COROS", ""), "suunto": ("Suunto", ""), "polar": ("Polar Flow", ""), "wahoo": ("Wahoo", ""),
}


def sync_instructions(brand: str) -> dict | None:
    """How to get this watch syncing through Intervals.icu, or None if it can't (send files instead)."""
    if brand not in SYNC_APPS:
        return None
    app, push = SYNC_APPS[brand]
    return {"via": "intervals.icu", "how": SYNC_HOW.format(app=app, push=push)}


def complete_intervals_link(conn, rid: int) -> dict | None:
    """For an approved link: if Coach Andy's key can now reach the athlete, go live (history,
    wellness, calendar) and turn their Intervals.icu threshold into a zones request.
    None while they still haven't accepted the coaching request."""
    athlete = intervals.check_access(conn, rid)
    if athlete is None:
        return None
    out = intervals.activate(conn, rid)
    r = conn.execute("SELECT zones_locked FROM runners WHERE runner_id=?", (rid,)).fetchone()
    pending = conn.execute("SELECT 1 FROM approvals WHERE runner_id=? AND kind='zones' AND status='pending'",
                           (rid,)).fetchone()
    z = intervals.zones_from_athlete(athlete)
    if z and not r["zones_locked"] and not pending:
        out["zones_request"] = propose_zones(
            conn, rid, z["zones"], z["max_hr"],
            f"Intervals.icu run settings: threshold {z['lthr']}"
            + (f", max {z['max_hr']}" if z["max_hr"] else "")
            + ". Check these are the runner's real numbers and not Intervals.icu defaults.")
    return out


def intervals_link_ready(rid: int) -> bool:
    """An approved link: once the athlete has accepted Coach Andy as coach, bring in their
    history, put any programme on their calendar, ask Coach Andy to confirm the zones from
    their Intervals.icu settings, and tell them it's working."""
    from . import hermes, record
    from .db import session
    with session() as conn:
        out = complete_intervals_link(conn, rid)
        if out is None:
            return False
        runner = dict(conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone())
    record.schedule_export(rid)
    c = out["activities"]
    hermes.notify_coach(f"{runner['name']} is connected to Intervals.icu: {c['stored']} activities brought in"
                        + (f", zones request [{out['zones_request']}] from their Intervals.icu threshold"
                           if out.get("zones_request") else "") + ".")
    if runner.get("hermes_api_key"):
        hermes.run_and_deliver(
            runner, f"Their Intervals.icu account is now connected: {c['stored']} past activities came in "
            f"({c['duplicate']} were already here). Tell them in two or three sentences that runs will now "
            "arrive on their own after each sync, then carry on with onboarding if it isn't finished "
            "(get_onboarding_status).", kind="intervals_connected")
    return True
