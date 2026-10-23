"""HTTP service: health check and scheduled-job endpoints.

Activities arrive as files the runner sends to the bot (see app/upload.py), not from here.

Run:  uvicorn app.main:app --port 8000
"""
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from . import hermes, hevy, plans, queries, record, trends
from .db import connect, init_db, session

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rac")

@asynccontextmanager
async def lifespan(_app):
    conn = connect()
    init_db(conn)
    conn.close()
    yield


app = FastAPI(title="Runner Assistant Coach data service", lifespan=lifespan)


# ---- Hevy webhook (public through the tunnel: /hevy/webhook only) --------------

@app.post("/hevy/webhook")
async def hevy_webhook(request: Request, tasks: BackgroundTasks):
    """Hevy needs 200 within 5 s: check who it's for, queue the work, answer."""
    with session() as conn:
        runner = hevy.runner_for_secret(conn, request.headers.get("authorization", ""))
    if runner is None:
        raise HTTPException(401)
    try:
        body = await request.json()
        workout_id = str(body["workoutId"])
    except Exception:
        raise HTTPException(400) from None
    if not workout_id.replace("-", "").isalnum() or len(workout_id) > 64:
        raise HTTPException(400)
    tasks.add_task(_hevy_workout, dict(runner), workout_id)
    return {"ok": True}


def _hevy_workout(runner: dict, workout_id: str) -> None:
    try:
        workout = hevy.fetch_workout(runner["api_key"], workout_id)
        with session() as conn:
            res = hevy.store(conn, runner, workout)
        record.schedule_export(runner["runner_id"])
        if res["status"] != "duplicate" and runner.get("onboarded_at") and runner.get("hermes_api_key"):
            hermes.run_and_deliver(
                runner, "A strength workout was just saved in Hevy and is now in the record "
                f"({res['date']}, {res['title']}, {res['sets']} working sets"
                + (", edited" if res["status"] == "updated" else "") + "). Give the strength debrief: "
                "call get_last_strength_session and get_strength_review, follow the strength guidance, "
                "one tip at most, then save_report.", kind="strength_debrief")
    except Exception:
        log.exception("Hevy workout %s for runner %s could not be processed", workout_id, runner["runner_id"])


@app.get("/health")
def health():
    return {"ok": True}


# ---- Scheduled jobs (call from cron with JOBS_TOKEN) -----------------------

def _check_jobs_token(request: Request) -> None:
    want = os.environ.get("JOBS_TOKEN", "")
    got = request.headers.get("authorization", "")[7:]
    if not want or not hmac.compare_digest(got, want):
        raise HTTPException(401)


def escalate_load(conn, today: date | None = None) -> list[str]:
    """Load flags that go to Coach Andy without waiting for Sarah: red this week, or amber
    three weeks running. Amber alone goes to the runner only, in the weekly report."""
    today = today or date.today()
    start = queries.week_start(today)
    sent = []
    for r in conn.execute("SELECT * FROM runners WHERE active=1 AND onboarded_at IS NOT NULL"
                          " AND focus IN ('running','hybrid')").fetchall():
        rid = r["runner_id"]
        flags = queries.week_summary(conn, rid, start).get("load") or trends.load(conn, rid, start)  # also saves the week
        levels = [x[0] for x in conn.execute(
            "SELECT flag_level FROM weekly_summary WHERE runner_id=? AND week_start<? ORDER BY week_start DESC LIMIT 2",
            (rid, start.isoformat()))]
        why = None
        if flags["level"] == "red":
            why = "Red load flag this week: " + "; ".join(flags["signs"])
        elif flags["level"] == "amber" and levels == ["amber", "amber"]:
            why = "Amber load flag three weeks running: " + "; ".join(flags["signs"])
        if why:
            aid = queries.create_alert(conn, rid, "volume_jump", why)
            hermes.notify_coach(f"Alert #{aid} for {r['name']}: {why}\nReply here and I'll pass your answer on.")
            sent.append(r["name"])
    return sent


@app.post("/jobs/weekly")
def job_weekly(request: Request, tasks: BackgroundTasks):
    _check_jobs_token(request)
    with session() as conn:
        escalate_load(conn)
    tasks.add_task(_for_each_runner, "It's the end of the week. Call get_week_summary and send the weekly report "
                   "for this person's focus. Running (focus running or hybrid): distance, time, time in "
                   "each heart rate zone (by name, in minutes) and the mileage check; with vs_programme, "
                   "planned against actual km, sessions done and missed, and the long run against its "
                   "target. Strength (focus strength or hybrid): sessions, sets per muscle group and how "
                   "the lifts are progressing from strength_review; with strength_vs_programme, sessions "
                   "done and missed. A lifter gets no running numbers. Use `load`: amber means mention it "
                   "gently to the runner (Coach Andy is told automatically about red, and about amber "
                   "three weeks running); red means a kind, calm message to ease off. If `taper` is "
                   "present, give that week's taper target. One focus for next week. Then rewrite the "
                   "runner summary.", kind="weekly")
    return {"queued": True}


@app.post("/jobs/monthly")
def job_monthly(request: Request, tasks: BackgroundTasks):
    """1st of the month: last month's report (includes the fitness check)."""
    _check_jobs_token(request)
    tasks.add_task(_for_each_runner, "It's the start of a new month. Call get_month_summary for last month "
                   "and send the monthly report for this person's focus. Running: distance and time "
                   "against the month before, how the weeks built, time in each heart rate zone, the "
                   "longest run, any time trial or race, and the fitness check with its progress status "
                   "(green, yellow or grey, never red). Strength: sessions and sets, and which lifts went "
                   "up, held or stalled (lifts) and any new records (strength_records). For runners, use "
                   "`progress`: the status colour and what drove it, and name one likely reason if "
                   "anything has stagnated. Add how the month went against any programme on file. "
                   "Finish with one focus for the month ahead.", kind="monthly")
    return {"queued": True}


@app.post("/jobs/fitness")
def job_fitness(request: Request, tasks: BackgroundTasks):
    _check_jobs_token(request)
    tasks.add_task(_for_each_runner, "Run the 4-weekly fitness check with get_fitness_trend and tell the runner "
                   "the verdict in two or three lines. Be gentle if it is dipping.", kind="fitness")
    return {"queued": True}


def _for_each_runner(prompt: str, kind: str = "job") -> None:
    with session() as conn:
        runners = conn.execute("SELECT * FROM runners WHERE active=1 AND hermes_api_key IS NOT NULL"
                               " AND onboarded_at IS NOT NULL").fetchall()
    for r in runners:
        try:
            hermes.run_and_deliver(r, prompt, kind=kind)
        except Exception:
            log.exception("job failed for runner %s", r["runner_id"])


@app.post("/jobs/session-preview")
def job_session_preview(request: Request, tasks: BackgroundTasks):
    """Evening before a programme session (guided runners only): a short preview."""
    _check_jobs_token(request)
    with session() as conn:
        due = [r for r in conn.execute("SELECT * FROM runners WHERE active=1 AND hermes_api_key IS NOT NULL"
                                       " AND onboarded_at IS NOT NULL")
               if plans.tomorrow_session(conn, r["runner_id"])]
    for r in due:
        tasks.add_task(_deliver, r, "Tomorrow there is a session in this person's programme. Call "
                       "get_my_sessions and send a short preview: what it is and what to aim for (distance "
                       "and heart rate or effort for a run; the main lifts for a strength session). Two or "
                       "three sentences, no more.", kind="session_preview")
    return {"queued": len(due)}


def _deliver(runner, prompt: str, kind: str = "job") -> None:
    try:
        hermes.run_and_deliver(runner, prompt, kind=kind)
    except Exception:
        log.exception("job failed for runner %s", runner["runner_id"])


NUDGE_DAYS = 5


@app.post("/jobs/nudge")
def job_nudge(request: Request, tasks: BackgroundTasks):
    """Daily: a gentle reminder to runners who haven't sent anything for NUDGE_DAYS days
    (at most once per NUDGE_DAYS). Coach Andy hears about 7+ days via /jobs/quiet-runners."""
    _check_jobs_token(request)
    with session() as conn:
        due = conn.execute(
            "SELECT r.* FROM runners r WHERE r.active=1 AND r.hermes_api_key IS NOT NULL"
            " AND r.onboarded_at IS NOT NULL"
            " AND COALESCE((SELECT MAX(date) FROM (SELECT date FROM runs WHERE runner_id=r.runner_id"
            "   UNION ALL SELECT date FROM strength_sessions WHERE runner_id=r.runner_id)), r.onboarded_at)"
            "   < date('now', ?)"
            " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.runner_id=r.runner_id AND m.type='nudge'"
            "   AND m.date >= datetime('now', ?))",
            (f"-{NUDGE_DAYS} days", f"-{NUDGE_DAYS} days")).fetchall()
    for r in due:
        tasks.add_task(_deliver, r, "The runner hasn't sent a run or workout for a few days. Send one short, "
                       "friendly check-in: ask how things are going and remind them they can send their "
                       "latest .fit file or type a gym session. No pressure, no guilt, one or two sentences.",
                       kind="nudge")
    return {"queued": len(due)}


@app.post("/jobs/records")
def job_records(request: Request):
    """Nightly: rebuild every runner's own record file from runners.db."""
    _check_jobs_token(request)
    with session() as conn:
        ids = [r[0] for r in conn.execute("SELECT runner_id FROM runners")]
        for rid in ids:
            record.export_runner(conn, rid)
    return {"records": len(ids)}


@app.post("/jobs/quiet-runners")
def job_quiet(request: Request):
    """Alert the coach about runners with no activity for 7+ days."""
    _check_jobs_token(request)
    with session() as conn:
        quiet = conn.execute(
            "SELECT r.runner_id, r.name, MAX(x.date) last FROM runners r LEFT JOIN ("
            " SELECT runner_id, date FROM runs UNION ALL SELECT runner_id, date FROM strength_sessions) x"
            " ON x.runner_id=r.runner_id WHERE r.active=1 GROUP BY r.runner_id"
            " HAVING last IS NULL OR last < date('now','-7 days')").fetchall()
        for q in quiet:
            queries.create_alert(conn, q["runner_id"], "quiet_runner", f"No activity since {q['last'] or 'joining'}")
    if quiet:
        hermes.notify_coach("Quiet runners (7+ days): " + ", ".join(q["name"] for q in quiet))
    return {"quiet": len(quiet)}
