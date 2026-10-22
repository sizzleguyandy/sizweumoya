"""HTTP service: health check and scheduled-job endpoints.

Activities arrive as files the runner sends to the bot (see app/upload.py), not from here.

Run:  uvicorn app.main:app --port 8000
"""
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from . import hermes, plans, queries
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


@app.get("/health")
def health():
    return {"ok": True}


# ---- Scheduled jobs (call from cron with JOBS_TOKEN) -----------------------

def _check_jobs_token(request: Request) -> None:
    want = os.environ.get("JOBS_TOKEN", "")
    got = request.headers.get("authorization", "")[7:]
    if not want or not hmac.compare_digest(got, want):
        raise HTTPException(401)


@app.post("/jobs/weekly")
def job_weekly(request: Request, tasks: BackgroundTasks):
    _check_jobs_token(request)
    tasks.add_task(_for_each_runner, "It's the end of the week. Call get_week_summary and send the weekly "
                   "summary: distance, time, time in each heart rate zone (by name, in minutes), the "
                   "mileage check and one focus. If coaching_mode is guided, compare the week with "
                   "vs_programme: planned against actual km, sessions done and missed, and the long "
                   "run against its target. Then rewrite the runner summary.")
    return {"queued": True}


@app.post("/jobs/fitness")
def job_fitness(request: Request, tasks: BackgroundTasks):
    _check_jobs_token(request)
    tasks.add_task(_for_each_runner, "Run the 4-weekly fitness check with get_fitness_trend and tell the runner "
                   "the verdict in two or three lines. Be gentle if it is dipping.")
    return {"queued": True}


def _for_each_runner(prompt: str) -> None:
    with session() as conn:
        runners = conn.execute("SELECT * FROM runners WHERE active=1 AND hermes_api_key IS NOT NULL").fetchall()
    for r in runners:
        try:
            hermes.run_and_deliver(r, prompt)
        except Exception:
            log.exception("job failed for runner %s", r["runner_id"])


@app.post("/jobs/session-preview")
def job_session_preview(request: Request, tasks: BackgroundTasks):
    """Evening before a programme session (guided runners only): a short preview."""
    _check_jobs_token(request)
    with session() as conn:
        due = [r for r in conn.execute("SELECT * FROM runners WHERE active=1 AND hermes_api_key IS NOT NULL")
               if plans.tomorrow_session(conn, r["runner_id"])]
    for r in due:
        tasks.add_task(_deliver, r, "Tomorrow there is a session in the runner's programme. Call "
                       "get_my_sessions and send a short preview: what it is, the distance, and the heart "
                       "rate or effort to aim for. Two or three sentences, no more.")
    return {"queued": len(due)}


def _deliver(runner, prompt: str) -> None:
    try:
        hermes.run_and_deliver(runner, prompt)
    except Exception:
        log.exception("job failed for runner %s", runner["runner_id"])


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
