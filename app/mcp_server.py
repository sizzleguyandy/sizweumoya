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

from mcp.server.mcpserver import Context, MCPServer

from datetime import date as _date

from . import hermes, ingest, queries, upload
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


# ---- runner tools ----------------------------------------------------------

@mcp.tool()
def get_my_profile(ctx: Context) -> dict:
    """Runner profile: name, HR zones, goal, units, tier."""
    with session() as conn:
        return queries.profile(conn, _rid(ctx))


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
    with session() as conn:
        try:
            return upload.process_path(conn, runner, file_path, note)
        except upload.UploadRejected as e:
            return {"error": str(e)}


@mcp.tool()
def log_workout(ctx: Context, text: str, date: str = "") -> dict:
    """Store a strength workout the runner typed, one exercise per line, e.g.
    "Squat 3x8 60kg; RDL 3x10 50kg; Plank 3x45s". `date` is YYYY-MM-DD (default today).
    Returns how many sets were understood and any lines that weren't."""
    when = date or _date.today().isoformat()
    try:
        _date.fromisoformat(when)
    except ValueError:
        return {"error": "date must be YYYY-MM-DD"}
    with session() as conn:
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (_rid(ctx),)).fetchone()
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
    """This week's km, runs, strength sessions, easy %, and mileage change vs last week."""
    with session() as conn:
        return queries.week_summary(conn, _rid(ctx))


@mcp.tool()
def get_fitness_trend(ctx: Context) -> dict:
    """Easy pace at the same HR, last 4 weeks vs previous 4: fitter / steady / dipping / insufficient."""
    with session() as conn:
        return queries.fitness_trend(conn, _rid(ctx))


@mcp.tool()
def get_last_strength_session(ctx: Context) -> dict | None:
    """Most recent Hevy session with all sets."""
    with session() as conn:
        return queries.last_strength_session(conn, _rid(ctx))


@mcp.tool()
def get_lift_history(ctx: Context, exercise: str) -> list[dict]:
    """Per-session top weight and reps for one exercise (partial name match)."""
    with session() as conn:
        return queries.lift_history(conn, _rid(ctx), exercise)


@mcp.tool()
def get_recent_tips(ctx: Context) -> list[dict]:
    """Tips already given, so you don't repeat yourself."""
    with session() as conn:
        return queries.recent_tips(conn, _rid(ctx))


@mcp.tool()
def record_tip(ctx: Context, rule: str, text: str) -> str:
    """Log the one tip you just gave (rule id from the stats, and your wording)."""
    with session() as conn:
        queries.record_tip(conn, _rid(ctx), rule, text)
    return "ok"


@mcp.tool()
def get_notes_and_summary(ctx: Context) -> dict:
    """Read first: the runner summary and active coach notes."""
    with session() as conn:
        return queries.notes_and_summary(conn, _rid(ctx))


@mcp.tool()
def add_coach_note(ctx: Context, category: str, note: str, review_by: str | None = None) -> int:
    """Save an observation. category: pattern, preference, health, life, motivation, milestone.
    For health notes set review_by (YYYY-MM-DD) and also call alert_coach."""
    with session() as conn:
        return queries.add_note(conn, _rid(ctx), category, note, review_by=review_by)


@mcp.tool()
def update_coach_note(ctx: Context, note_id: int, status: str) -> bool:
    """Mark one of this runner's notes resolved or outdated."""
    with session() as conn:
        return queries.set_note_status(conn, _rid(ctx), note_id, status)


@mcp.tool()
def save_runner_summary(ctx: Context, summary: str) -> str:
    """Replace the short 'who is this runner right now' snapshot (weekly)."""
    with session() as conn:
        queries.save_summary(conn, _rid(ctx), summary)
    return "ok"


@mcp.tool()
def get_current_plan(ctx: Context) -> dict:
    """Coach-written workouts from 3 days ago to 14 days ahead (paid tiers)."""
    with session() as conn:
        return queries.current_plan(conn, _rid(ctx))


@mcp.tool()
def propose_plan_change(ctx: Context, reason: str, proposal: str) -> str:
    """Ask the head coach to approve a change. Nothing changes until they decide."""
    with session() as conn:
        r = _runner(ctx)
        aid = queries.create_approval(conn, r["runner_id"], reason, proposal)
    hermes.notify_coach(f"Approval needed [{aid}] for {r['name']}\nWhy: {reason}\nProposal: {proposal}")
    return f"Sent to your coach for approval (request {aid})."


@mcp.tool()
def alert_coach(ctx: Context, type: str, message: str) -> str:
    """Flag something to the head coach. type: injury, volume_jump, quiet_runner, other."""
    with session() as conn:
        r = _runner(ctx)
        queries.create_alert(conn, r["runner_id"], type, message)
    hermes.notify_coach(f"Alert ({type}) for {r['name']}: {message}")
    return "Coach alerted."


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
    _deliver_decision(runner, decision, proposal, note)
    return f"Recorded: {decision}. Runner notified."



@mcp.tool()
def coach_open_alerts(ctx: Context) -> list[dict]:
    """Coach only: unhandled alerts."""
    _require_coach(ctx)
    with session() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT a.id, r.name, a.date, a.type, a.message FROM coach_alerts a"
            " JOIN runners r ON r.runner_id=a.runner_id WHERE a.handled=0 ORDER BY a.date DESC")]


if __name__ == "__main__":
    mcp.run("streamable-http", host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8100")))
