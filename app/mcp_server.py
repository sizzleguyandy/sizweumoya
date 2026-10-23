"""MCP tools for Hermes. Runner tools act only on the runner resolved from headers.

No tool takes a runner id: the runner comes from the calling profile's identity
(``identity_header: {name: x-hermes-profile, value_from: profile}``) plus its own bearer
token, both checked against ``runners`` in ``app.identity``.

Run:  python -m app.mcp_server   (streamable HTTP on :8100/mcp)
"""
import hmac
import logging
import os
import threading
from pathlib import Path
from contextlib import contextmanager

from mcp.server.mcpserver import Context, MCPServer

from datetime import date as _date

from . import hermes, ingest, intervals, keyboard, onboarding, paces, plans, queries, record, strength, trends, upload
from .config import settings
from .db import session
from .identity import AccessDenied, hash_token, resolve_runner

log = logging.getLogger(__name__)

mcp = MCPServer(
    name="runner-assistant-coach",
    instructions=(
        "Tools for an assistant running coach. Data always belongs to the runner you are talking to. "
        "Never create or change a training plan: use propose_plan_change and wait for the head coach."
    ),
)


def _runner(ctx: Context):
    with session() as conn:
        return resolve_runner(conn, ctx.headers, settings.mcp_profile_header)


def _rid(ctx: Context) -> int:
    try:
        return _runner(ctx)["runner_id"]
    except AccessDenied as e:
        raise PermissionError(f"access denied: {e}") from None


@contextmanager
def _write(rid: int):
    """A session for tools that change a runner's data. Afterwards the runner's own record
    file is rebuilt, so it always holds everything."""
    with session() as conn:
        yield conn
        conn.commit()
    record.schedule_export(rid)


# ---- runner tools ----------------------------------------------------------

@mcp.tool()
def get_my_profile(ctx: Context) -> dict:
    """Profile: name, HR zones, goal, units, tier, and `coaching`: focus (running, strength or
    hybrid) and, per discipline, "monitor" (they log their own training, you give feedback) or
    "guided" (a programme from Coach Andy is on file and you take them through it).
    Read this first; it decides how you work with this person."""
    with session() as conn:
        rid = _rid(ctx)
        return queries.profile(conn, rid) | {"coaching": plans.mode(conn, rid)}


@mcp.tool()
def get_my_sessions(ctx: Context) -> dict:
    """Programme sessions around today (last, today, next) for each discipline that has a
    programme from Coach Andy: runs with km, kind, HR range and climb; strength sessions with
    their exercises. Returns no sessions for training the person logs themselves."""
    with session() as conn:
        return plans.sessions_around(conn, _rid(ctx))


@mcp.tool()
def submit_activity_file(ctx: Context, file_path: str, note: str = "") -> dict:
    """Store a .fit file (or Garmin .zip) the runner just sent. Pass the local path Hermes gave
    you for the attachment, and the runner's message caption as `note` (e.g. "hot, legs tired").

    Returns what was stored. For one run: date, distance, duration, run_type and `short`
    (short runs get no debrief). For many files (history import): counts and date range;
    then call get_baseline. Duplicates are ignored, so re-sending is safe."""
    try:
        runner = _runner(ctx)
    except AccessDenied as e:
        raise PermissionError(f"access denied: {e}") from None
    with _write(runner["runner_id"]) as conn:
        try:
            return upload.process_path(conn, runner, file_path, note)
        except upload.UploadRejected as e:
            return {"error": str(e)}


@mcp.tool()
def log_workout(ctx: Context, text: str, date: str = "") -> dict:
    """Store a strength workout the person typed or pasted. Pass their text EXACTLY as they sent
    it, never rewritten or summarised: the parser reads "Squat 3x8 60kg", "Press 3x10 + 2x7 30kg",
    "Plank 3x45s" and Hevy's shared workout text ("Set 1: 60 kg x 5" lines) itself.
    `date` is YYYY-MM-DD (default today). Returns how many sets were stored and any lines it
    didn't understand; ask about those lines rather than guessing. `already_synced_from: hevy`
    means that day's workout already arrived from Hevy: nothing was stored, show that session."""
    when = date or _date.today().isoformat()
    try:
        _date.fromisoformat(when)
    except ValueError:
        return {"error": "date must be YYYY-MM-DD"}
    rid = _rid(ctx)
    with _write(rid) as conn:
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
        return ingest.store_strength_text(conn, runner, text, when)


@mcp.tool()
def get_baseline(ctx: Context) -> dict:
    """Onboarding snapshot from the last 8 weeks of uploaded files: average km and runs per
    week, longest run, easy %, easy pace and heart rate, max HR seen, strength sessions.
    `enough_data` is false with fewer than 6 runs."""
    with session() as conn:
        return queries.baseline(conn, _rid(ctx))


@mcp.tool()
def get_recent_runs(ctx: Context, n: int = 5) -> list[dict]:
    """Last n runs with pace, time in zones (seconds), HR drift, run type and triggered tip rules."""
    with session() as conn:
        return queries.recent_runs(conn, _rid(ctx), n)


@mcp.tool()
def get_week_summary(ctx: Context) -> dict:
    """This week's km, time, runs, strength sessions, easy %, mileage change vs last week,
    time in each named HR zone, longest run and easy runs over the HR cap. In guided mode also
    `vs_programme`: planned vs actual km, sessions done/missed/to come, long run vs target."""
    with session() as conn:
        return queries.week_summary(conn, _rid(ctx))


@mcp.tool()
def get_month_summary(ctx: Context, month: str = "") -> dict:
    """Monthly report for a calendar month, `month` as YYYY-MM (default: last month).
    Totals vs the previous month, km per week, time, time in each named HR zone, longest run,
    benchmarks, strength sessions, the fitness check and progress_status (green/yellow/grey,
    never red). In guided mode also vs_programme for the month."""
    if month:
        try:
            y, m = (int(x) for x in month.split("-"))
            _date(y, m, 1)
        except ValueError:
            return {"error": "month must be YYYY-MM"}
    else:
        first = _date.today().replace(day=1)
        y, m = (first.year - 1, 12) if first.month == 1 else (first.year, first.month - 1)
    with session() as conn:
        return queries.month_summary(conn, _rid(ctx), y, m)


@mcp.tool()
def get_my_progress(ctx: Context) -> dict:
    """The big picture, with or without a programme. running: progress status (green/yellow/grey,
    never red) from four signals (easy pace at the same heart rate, weekly km, longest run, easy
    share) plus stagnation flags; load: this week's load and injury traffic light with its signs;
    taper: suggested taper if a race is within 4 weeks and there's no programme. strength:
    records per lift (best weight, estimated 1RM, new_record) and the strength review."""
    rid = _rid(ctx)
    with session() as conn:
        m = plans.mode(conn, rid)
        out: dict = {"focus": m["focus"]}
        if m["focus"] in ("running", "hybrid"):
            out["running"] = trends.progress(conn, rid)
            out["load"] = trends.load(conn, rid, queries.week_start(_date.today()))
            if m["running"] != "guided":
                out["taper"] = trends.taper(conn, rid)
        if m["focus"] in ("strength", "hybrid"):
            out["strength"] = {"records": trends.strength_records(conn, rid), "review": strength.review(conn, rid)}
        return out


@mcp.tool()
def get_fitness_trend(ctx: Context) -> dict:
    """Easy pace at the same HR, last 4 weeks vs previous 4: fitter / steady / dipping / insufficient."""
    with session() as conn:
        return queries.fitness_trend(conn, _rid(ctx))


@mcp.tool()
def get_last_strength_session(ctx: Context) -> dict | None:
    """Most recent strength session with all sets."""
    with session() as conn:
        return queries.last_strength_session(conn, _rid(ctx))


@mcp.tool()
def get_lift_history(ctx: Context, exercise: str) -> list[dict]:
    """Per-session top weight and reps for one exercise (partial name match)."""
    with session() as conn:
        return queries.lift_history(conn, _rid(ctx), exercise)


@mcp.tool()
def get_strength_review(ctx: Context) -> dict:
    """Strength training against the evidence base (ACSM 2026): hard sets and training days per
    muscle group over the last 7 days, and per lift the trend (load up / reps up / same / down)
    with `stalled` after 4 identical sessions. Includes the guide values and what the data
    cannot show (reps in reserve, rest, technique)."""
    with session() as conn:
        return strength.review(conn, _rid(ctx))


@mcp.tool()
def get_recent_tips(ctx: Context) -> list[dict]:
    """Tips already given, so you don't repeat yourself."""
    with session() as conn:
        return queries.recent_tips(conn, _rid(ctx))


@mcp.tool()
def record_tip(ctx: Context, rule: str, text: str) -> str:
    """Log the one tip you just gave (rule id from the stats, and your wording)."""
    rid = _rid(ctx)
    with _write(rid) as conn:
        queries.record_tip(conn, rid, rule, text)
    return "ok"


@mcp.tool()
def get_notes_and_summary(ctx: Context) -> dict:
    """Read first: the runner summary and active coach notes."""
    with session() as conn:
        return queries.notes_and_summary(conn, _rid(ctx))


@mcp.tool()
def add_coach_note(ctx: Context, category: str, note: str, review_by: str | None = None) -> int:
    """Save anything worth remembering about the runner to their record. category: pattern,
    preference, health, life, motivation, milestone, profile (facts: age, weight, usual run days,
    work shifts), gear (shoes, watch). For health notes set review_by (YYYY-MM-DD) and also call
    alert_coach."""
    rid = _rid(ctx)
    with _write(rid) as conn:
        return queries.add_note(conn, rid, category, note, review_by=review_by)


@mcp.tool()
def update_coach_note(ctx: Context, note_id: int, status: str) -> bool:
    """Mark one of this runner's notes resolved or outdated."""
    rid = _rid(ctx)
    with _write(rid) as conn:
        return queries.set_note_status(conn, rid, note_id, status)


@mcp.tool()
def save_runner_summary(ctx: Context, summary: str) -> str:
    """Replace the short 'who is this runner right now' snapshot (weekly)."""
    rid = _rid(ctx)
    with _write(rid) as conn:
        queries.save_summary(conn, rid, summary)
    return "ok"


@mcp.tool()
def get_current_plan(ctx: Context) -> dict:
    """Coach-written workouts from 3 days ago to 14 days ahead (paid tiers). Each has a status
    (planned/done/missed) and a target: km, kind (easy/long/workout/benchmark/race), HR range
    for quality work, climb for long runs. Easy and long runs are capped at the runner's
    easy_hr_cap (see get_my_profile)."""
    with session() as conn:
        return queries.current_plan(conn, _rid(ctx))


@mcp.tool()
def get_my_paces_and_zones(ctx: Context) -> dict:
    """The runner's heart rate zones and paces, recalculated from their latest data.
    hr_zones: five named bands (bpm). easy_hr_cap: the ceiling for easy and long runs.
    observed_easy: median pace and HR of recent outdoor easy runs under that ceiling.
    performance: from the best time trial or race in the last 120 days: VDOT, training paces
    (easy, marathon, threshold, interval) and race estimates. `available` false means no
    recent benchmark; say a flat 5K time trial would unlock it."""
    with session() as conn:
        return paces.profile(conn, _rid(ctx))


@mcp.tool()
def get_my_wellness(ctx: Context, days: int = 14) -> dict:
    """Daily wellness from the person's watch (via Intervals.icu): resting heart rate, HRV, sleep
    and weight, newest first, with 7-day averages. Rising resting HR or falling HRV over several
    days alongside heavy training is a recovery flag; one odd day is not."""
    with session() as conn:
        rid = _rid(ctx)
        rows_ = [dict(r) for r in conn.execute(
            "SELECT date, resting_hr, hrv, sleep_s, sleep_score, weight_kg FROM wellness WHERE runner_id=?"
            " ORDER BY date DESC LIMIT ?", (rid, max(1, min(days, 90))))]
    def avg(k, n=7):
        v = [r[k] for r in rows_[:n] if r[k]]
        return round(sum(v) / len(v), 1) if v else None
    return {"days": rows_, "avg_7d": {"resting_hr": avg("resting_hr"), "hrv": avg("hrv"),
                                      "sleep_h": round(avg("sleep_s") / 3600, 1) if avg("sleep_s") else None}}


@mcp.tool()
def get_my_races(ctx: Context) -> list[dict]:
    """Upcoming races: name, date, distance, climb, notes and days to go."""
    with session() as conn:
        return plans.races(conn, _rid(ctx))


@mcp.tool()
def propose_plan_change(ctx: Context, reason: str, proposal: str, session_date: str = "",
                        new_date: str = "") -> str:
    """Ask the head coach to approve a change. Nothing changes until they decide.
    To move a session to another day, also give session_date and new_date (YYYY-MM-DD): when
    Coach Andy approves, the programme (and the watch calendar) is updated automatically.
    Check the weekday of both dates with get_my_sessions before proposing."""
    r = _runner(ctx)
    kind, payload = "plan", None
    if session_date or new_date:
        try:
            a, b = _date.fromisoformat(session_date), _date.fromisoformat(new_date)
        except ValueError:
            return "Not sent: session_date and new_date must both be YYYY-MM-DD."
        kind, payload = "move", {"from": a.isoformat(), "to": b.isoformat()}
        proposal = f"{proposal} [move {a:%A %d %b} -> {b:%A %d %b}]"
    with _write(r["runner_id"]) as conn:
        aid = queries.create_approval(conn, r["runner_id"], reason, proposal, kind, payload)
    hermes.notify_coach(f"Approval needed [{aid}] for {r['name']}\nWhy: {reason}\nProposal: {proposal}\n"
                        f"Reply approve {aid} or reject {aid}.")
    return f"Sent to your coach for approval (request {aid})."


@mcp.tool()
def alert_coach(ctx: Context, type: str, message: str) -> str:
    """Flag something to the head coach. type: injury, volume_jump, quiet_runner, other."""
    r = _runner(ctx)
    with _write(r["runner_id"]) as conn:
        aid = queries.create_alert(conn, r["runner_id"], type, message)
    hermes.notify_coach(f"Alert #{aid} ({type}) for {r['name']}: {message}\n"
                        "Reply here and I'll pass your answer on.")
    return "Coach alerted."


# ---- the runner's record: save and find anything -----------------------------

@mcp.tool()
def save_report(ctx: Context, type: str, text: str, basis: dict | None = None) -> str:
    """Save to the runner's record every debrief, report or answer with numbers you send them.
    type: debrief, strength_debrief, weekly, monthly, baseline, taper, answer. text: exactly
    what you sent. basis: what it was based on, e.g. {"run_date": "2026-10-22", "rule": "hr_drift"}."""
    rid = _rid(ctx)
    with _write(rid) as conn:
        saved = queries.log_message(conn, rid, "out", type[:30], text, basis)
    return "saved" if saved else "already saved"


@mcp.tool()
def get_my_record(ctx: Context, section: str = "summary", since: str = "", until: str = "",
                  limit: int = 50) -> dict:
    """Look anything up in the person's record. section: summary (what's on file), insights
    (latest progress status, load flag, taper and lift records in one go), weekly_km,
    monthly_km, personal_bests, lift_records, runs, splits (per-km pace and HR), strength
    (every set), reports (everything sent to them), messages, notes, races, plan, weekly,
    fitness, tips. since/until (YYYY-MM-DD) apply to the date-based sections.
    Example: a question about Sunday's run -> section="splits", since=until=that date."""
    with session() as conn:
        return record.get_record(conn, _rid(ctx), section, since, until, limit)


@mcp.tool()
def send_my_data_file(ctx: Context) -> str:
    """The person asked for a copy of their data. Sends them their complete record as a .db
    file in this chat (it opens in free tools such as DB Browser for SQLite), with ready-made
    views: weekly_km, monthly_km, personal_bests, lift_records, and latest_insights."""
    r = _runner(ctx)

    def _work() -> None:
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as tmp:
                first = "".join(ch for ch in (r["name"].split() or ["my"])[0].lower() if ch.isalnum()) or "my"
                dest = Path(tmp) / f"{first}-training-record.db"
                with session() as conn:
                    record.export_runner(conn, r["runner_id"], dest)
                hermes.send_telegram_file(r["telegram_chat_id"], str(dest),
                                          "Your complete training record. Open it with DB Browser for SQLite.")
        except Exception:
            log.exception("data file for runner %s could not be sent", r["runner_id"])

    threading.Thread(target=_work, name=f"data-file-{r['runner_id']}", daemon=True).start()
    return "Your file is on its way; it should arrive in this chat in a moment."


@mcp.tool()
def request_data_deletion(ctx: Context, reason: str = "") -> str:
    """The runner asked for all their data to be deleted. Coach Andy is told and completes it
    (within 30 days, as the privacy policy says). Confirm kindly; don't try to talk them out of it."""
    r = _runner(ctx)
    with _write(r["runner_id"]) as conn:
        aid = queries.create_alert(conn, r["runner_id"], "deletion", f"Asked to delete all data. {reason}"[:500])
    hermes.notify_coach(f"Alert #{aid} (deletion) for {r['name']}: asked for all their data to be deleted. "
                        f"Run: python -m app.admin offboard --runner {r['runner_id']}")
    return "Coach Andy has been asked to delete your data."


# ---- onboarding ------------------------------------------------------------

@mcp.tool()
def get_onboarding_status(ctx: Context) -> dict:
    """Where this person is in onboarding. `next_step` is what to do next: consent, focus,
    watch, history, zones, races, health, finish. Steps that don't apply to their focus (watch,
    zones and races for a lifter) are already marked done. None once onboarded."""
    with session() as conn:
        return onboarding.status(conn, _rid(ctx))


@mcp.tool()
def record_consent(ctx: Context, agreed: bool) -> str:
    """Call only after the runner has clearly said yes to their data being used for coaching
    (heart rate counts as health data). agreed must be true."""
    if agreed is not True:
        return "Not recorded: the runner has to agree first."
    rid = _rid(ctx)
    with _write(rid) as conn:
        return onboarding.record_consent(conn, rid)


@mcp.tool()
def set_my_focus(ctx: Context, focus: str) -> str:
    """What this person trains, which decides how you coach them: running (runner), strength
    (lifter: strength guidelines only, no running reports) or hybrid (both). Ask, don't assume.
    Can be changed later if their training changes."""
    rid = _rid(ctx)
    try:
        with _write(rid) as conn:
            return "focus set to " + onboarding.set_focus(conn, rid, focus)
    except ValueError as e:
        return str(e)


@mcp.tool()
def set_my_watch(ctx: Context, brand: str) -> dict:
    """Save which watch the runner uses and get the instructions for sending files from it."""
    rid = _rid(ctx)
    with _write(rid) as conn:
        return onboarding.set_watch(conn, rid, brand)


@mcp.tool()
def mark_onboarding_step(ctx: Context, step: str) -> str:
    """Record an onboarding step that has no data of its own. step: history_fresh (runner has
    no history to send), no_zones (runner can't find their zones), races_asked (asked about
    races; save any with save_race first), health_asked (asked about injuries; save any as a
    health note and alert_coach first)."""
    notes = {"history_fresh": "history: starting fresh", "no_zones": "zones: none available",
             "races_asked": "races asked", "health_asked": "health asked"}
    if step not in notes:
        return f"step must be one of {sorted(notes)}"
    rid = _rid(ctx)
    with _write(rid) as conn:
        onboarding.mark(conn, rid, notes[step])
    return "ok"


@mcp.tool()
def save_race(ctx: Context, name: str, date: str, distance_km: float, note: str = "") -> dict:
    """Save a race the runner has entered or plans to run. date YYYY-MM-DD."""
    rid = _rid(ctx)
    try:
        with _write(rid) as conn:
            return onboarding.save_race(conn, rid, name, date, distance_km, note)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
def propose_my_zones(ctx: Context, zone_tops: list[int], max_hr: int | None = None,
                     source: str = "screenshot of the runner's watch zones") -> str:
    """Heart rate zones the runner showed you (e.g. a screenshot of their watch app).
    zone_tops: the top of zones 1-4 in bpm, e.g. [132, 144, 152, 162]. Coach Andy approves
    before they are used; tell the runner that."""
    r = _runner(ctx)
    try:
        with _write(r["runner_id"]) as conn:
            aid = onboarding.propose_zones(conn, r["runner_id"], zone_tops, max_hr, source)
    except ValueError as e:
        return f"Not sent: {e}"
    hermes.notify_coach(f"Approval needed [{aid}] for {r['name']}: heart rate zones {zone_tops}"
                        + (f", max HR {max_hr}" if max_hr else "") + f" (from {source}). "
                        f"Reply approve {aid} or reject {aid}.")
    return f"Sent to Coach Andy for approval (request {aid})."


@mcp.tool()
def finish_onboarding(ctx: Context) -> dict:
    """Last step: marks the runner as live and sends Coach Andy the new-runner summary.
    Needs consent. Returns anything that was skipped."""
    r = _runner(ctx)
    try:
        with _write(r["runner_id"]) as conn:
            res = onboarding.finish(conn, r["runner_id"])
    except ValueError as e:
        return {"error": str(e)}
    hermes.notify_coach("New runner: " + res["coach_summary"])
    try:
        keyboard.send(r)
    except Exception:
        log.exception("buttons could not be sent to runner %s", r["runner_id"])
    return {"onboarded": True, "skipped": res["missing"], "buttons_sent": [b for row in keyboard.BUTTONS for b in row]}


# ---- coach tools (coach profile only) -------------------------------------

def _require_coach(ctx: Context) -> None:
    h = {k.lower(): v for k, v in (ctx.headers or {}).items()}
    profile = h.get(settings.mcp_profile_header.lower(), "")
    token = h.get("authorization", "")[7:].strip()
    want_profile = os.environ.get("COACH_PROFILE", "coach")
    want_hash = os.environ.get("COACH_MCP_TOKEN_HASH", "")
    if not (want_hash and profile == want_profile and hmac.compare_digest(hash_token(token), want_hash)):
        raise PermissionError("coach tools are only available to the coach profile")


@mcp.tool()
def coach_pending_approvals(ctx: Context) -> list[dict]:
    """Coach only: pending approval requests across runners."""
    _require_coach(ctx)
    with session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT a.id, r.name, a.created, a.reason, a.proposal FROM approvals a"
            " JOIN runners r ON r.runner_id=a.runner_id WHERE a.status='pending' ORDER BY a.created")]


def _apply_move(conn, approval: dict) -> dict | None:
    """Apply an approved 'move' request to the programme and the watch calendar."""
    if approval.get("kind") != "move" or not approval.get("payload"):
        return None
    import json as _json
    p = _json.loads(approval["payload"])
    try:
        res = plans.move_session(conn, approval["runner_id"], p["from"], p["to"],
                                 note=f"approved request {approval['id']}")
    except plans.PlanChangeError as e:
        return {"error": str(e)}
    _resync_calendar(conn, approval["runner_id"], p["from"], p["to"])
    return res


def _resync_calendar(conn, rid: int, *days: str) -> None:
    """If the person's programme is on their Intervals.icu calendar, send the change there too."""
    if not conn.execute("SELECT 1 FROM intervals_links WHERE runner_id=?", (rid,)).fetchone():
        return
    try:
        intervals.reconcile(conn, rid)
    except Exception:
        log.exception("calendar update for runner %s failed", rid)


def _deliver_in_background(runner: dict, prompt: str, label: str) -> None:
    """Start a turn in the runner's profile on a background thread and deliver the reply.

    A turn costs a model round trip, so the coach's request must not wait on it.
    A delivery error is logged, never raised into the coach's tool call.
    """
    def _work() -> None:
        try:
            hermes.run_and_deliver(runner, prompt)
        except Exception:
            log.exception("%s for runner %s could not be delivered", label, runner.get("runner_id"))

    threading.Thread(target=_work, name=f"{label}-{runner.get('runner_id')}", daemon=True).start()


def _deliver_decision(runner: dict, decision: str, proposal: str, note: str) -> None:
    """Hand the decision to the runner's profile turn on a background thread.

    A turn costs a model round trip (and can wait on a tool call), so the coach's request
    must not sit on it: by the time this returns, the tool has already answered. The thread
    owns its own failures — a delivery error is logged, never raised into the decision.
    """
    prompt = (
        f"Your head coach has {decision} your proposed change ({proposal}). "
        f"Coach's note: {note or 'none'}. Tell the runner briefly and kindly."
    )

    def _work() -> None:
        try:
            hermes.run_and_deliver(runner, prompt)
        except Exception:
            log.exception("decision %s for runner %s could not be delivered",
                          decision, runner.get("runner_id"))

    threading.Thread(target=_work, name=f"coach-decide-{runner.get('runner_id')}",
                     daemon=True).start()


@mcp.tool()
def coach_decide(ctx: Context, approval_id: str, decision: str, note: str = "") -> str:
    """Coach only: approved, rejected or edited, against an existing request id.

    Records the decision and returns at once; the runner's profile turn (which can take a
    minute or two) is delivered by a background thread.
    """
    _require_coach(ctx)
    with session() as conn:
        row = queries.decide_approval(conn, approval_id, decision, note or None)
        if row is None:
            return "No pending request with that id."
        found = conn.execute("SELECT * FROM runners WHERE runner_id=?", (row["runner_id"],)).fetchone()
        if found is None:
            return f"Recorded: {decision}. Runner row is gone; nothing to notify."
        runner = dict(found)  # detach: the connection closes with this block
        proposal = row["proposal"]
        applied = None
        if decision == "approved":
            onboarding.apply_zones(conn, row)
            applied = _apply_move(conn, row)
    record.schedule_export(runner["runner_id"])
    if applied and applied.get("error"):
        note = (note + " " if note else "") + f"(The programme could not be updated: {applied['error']})"
    elif applied:
        note = (note + " " if note else "") + f"(Programme updated: moved to {applied['to']}.)"
    _deliver_decision(runner, decision, proposal, note)
    if applied:
        return (f"Recorded: {decision}. " + (f"Programme NOT updated: {applied['error']}" if applied.get("error")
                else f"Programme updated: {applied['moved']} moved from {applied['from']} to {applied['to']}.")
                + " Runner notified.")
    return f"Recorded: {decision}. Runner notified."



@mcp.tool()
def coach_reply(ctx: Context, message: str, alert_id: int = 0) -> str:
    """Coach only: answer an alert. Sarah passes Coach Andy's answer to that runner and the
    alert is marked handled. Leave alert_id at 0 only when exactly one alert is open;
    otherwise the open alerts are listed so you can ask which one is meant."""
    _require_coach(ctx)
    message = (message or "").strip()
    if not message:
        return "The reply is empty."
    with session() as conn:
        if alert_id:
            row = conn.execute("SELECT * FROM coach_alerts WHERE id=?", (alert_id,)).fetchone()
            if row is None:
                return f"No alert #{alert_id}."
        else:
            open_ = conn.execute("SELECT a.id, r.name, a.message FROM coach_alerts a JOIN runners r"
                                 " ON r.runner_id=a.runner_id WHERE a.handled=0 ORDER BY a.date DESC").fetchall()
            if len(open_) != 1:
                if not open_:
                    return "There are no open alerts to reply to."
                listing = "; ".join(f"#{a['id']} {a['name']}: {a['message'][:80]}" for a in open_)
                return f"More than one alert is open, which one is this for? {listing}"
            row = conn.execute("SELECT * FROM coach_alerts WHERE id=?", (open_[0]["id"],)).fetchone()
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (row["runner_id"],)).fetchone()
        if runner is None:
            return "That runner no longer exists."
        runner = dict(runner)
        conn.execute("UPDATE coach_alerts SET handled=1 WHERE id=?", (row["id"],))
        conn.commit()
        queries.log_message(conn, runner["runner_id"], "out", "coach_reply", message[:2000])
        about = row["message"]
    record.schedule_export(runner["runner_id"])
    prompt = (
        f"Coach Andy has answered something you raised ({about}). Coach Andy's answer: \"{message}\". "
        "Pass it on to the runner now, briefly and in your own voice, making clear it comes from "
        "Coach Andy. Keep every number and condition exactly as given; add nothing of your own."
    )
    _deliver_in_background(runner, prompt, "coach-reply")
    return f"Sent to {runner['name']} (alert #{row['id']} closed)."


def _coach_runner(conn, name: str):
    rows_ = conn.execute("SELECT * FROM runners WHERE active=1 AND lower(name) LIKE ?",
                         (f"%{(name or '').strip().lower()}%",)).fetchall()
    if len(rows_) != 1:
        names = ", ".join(r["name"] for r in conn.execute("SELECT name FROM runners WHERE active=1"))
        raise ValueError(f"say which runner: {names}")
    return rows_[0]


@mcp.tool()
def coach_runner_plan(ctx: Context, runner: str, days: int = 14) -> dict:
    """Coach only: a runner's programme sessions from 3 days ago to `days` ahead, with weekday,
    status and targets. `runner` is their name."""
    _require_coach(ctx)
    with session() as conn:
        try:
            r = _coach_runner(conn, runner)
        except ValueError as e:
            return {"error": str(e)}
        out = queries.current_plan(conn, r["runner_id"], max(1, min(days, 90)))
        for w in out["workouts"]:
            w["weekday"] = _date.fromisoformat(w["date"]).strftime("%A")
        return {"runner": r["name"], **out}


@mcp.tool()
def coach_move_session(ctx: Context, runner: str, from_date: str, to_date: str) -> str:
    """Coach only: move one of a runner's programme sessions to another day (YYYY-MM-DD).
    Updates their programme and their watch calendar, and tells the runner."""
    _require_coach(ctx)
    with session() as conn:
        try:
            r = dict(_coach_runner(conn, runner))
            res = plans.move_session(conn, r["runner_id"], from_date, to_date, note="moved by Coach Andy")
        except (ValueError, plans.PlanChangeError) as e:
            return f"Not moved: {e}"
        _resync_calendar(conn, r["runner_id"], from_date, to_date)
    record.schedule_export(r["runner_id"])
    extra = f" Also on that day: {'; '.join(res['also_on_that_day'])}." if res["also_on_that_day"] else ""
    _deliver_in_background(r, f"Coach Andy has moved your session \"{res['moved']}\" from {res['from']} to "
                           f"{res['to']}. Tell the runner briefly, with the new day.", "coach-move")
    return f"Moved {res['moved']} from {res['from']} to {res['to']}.{extra} Runner notified."


@mcp.tool()
def coach_open_alerts(ctx: Context) -> list[dict]:
    """Coach only: unhandled alerts."""
    _require_coach(ctx)
    with session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT a.id, r.name, a.date, a.type, a.message FROM coach_alerts a"
            " JOIN runners r ON r.runner_id=a.runner_id WHERE a.handled=0 ORDER BY a.date DESC")]


if __name__ == "__main__":
    from .db import connect, init_db

    _c = connect()
    init_db(_c)  # brings an older runners.db up to the current schema before serving
    _c.close()
    mcp.run("streamable-http", host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8100")))
