"""HTTP service: Strava OAuth + webhook, and scheduled-job endpoints.

Run:  uvicorn app.main:app --port 8000
"""
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import hermes, ingest, queries, strava
from .config import settings
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


# ---- Strava OAuth ----------------------------------------------------------

def _sign(runner_id: int) -> str:
    key = (settings.strava_client_secret or "dev").encode()
    return f"{runner_id}.{hmac.new(key, str(runner_id).encode(), 'sha256').hexdigest()[:24]}"


def _verify_state(state: str) -> int:
    rid, _, _ = state.partition(".")
    if not rid.isdigit() or not hmac.compare_digest(_sign(int(rid)), state):
        raise HTTPException(400, "bad state")
    return int(rid)


@app.get("/strava/connect/{runner_id}")
def strava_connect(runner_id: int):
    """Link sent to the runner during onboarding (state is signed so it can't be forged)."""
    return {"url": strava.authorize_url(_sign(runner_id))}


@app.get("/strava/callback", response_class=PlainTextResponse)
def strava_callback(code: str, state: str, scope: str = ""):
    rid = _verify_state(state)
    if "activity:read" not in scope:
        return "Please allow activity access so your coach can see your runs."
    tok = strava.exchange_code(code)
    with session() as conn:
        strava.save_tokens(conn, rid, tok)
        conn.execute("UPDATE runners SET strava_athlete_id=? WHERE runner_id=?", (tok["athlete"]["id"], rid))
    return "Strava connected. You can close this page."


# ---- Strava webhook --------------------------------------------------------

@app.get("/strava/webhook")
def strava_verify(mode: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
                  challenge: str = Query(alias="hub.challenge")):
    if mode != "subscribe" or not hmac.compare_digest(token, settings.strava_verify_token):
        raise HTTPException(403)
    return {"hub.challenge": challenge}


@app.post("/strava/webhook")
async def strava_event(request: Request, tasks: BackgroundTasks):
    ev = await request.json()
    if ev.get("object_type") == "activity" and ev.get("aspect_type") in ("create", "update"):
        tasks.add_task(handle_activity, ev["owner_id"], ev["object_id"], ev["aspect_type"])
    elif ev.get("object_type") == "athlete" and (ev.get("updates") or {}).get("authorized") == "false":
        tasks.add_task(handle_deauth, ev["owner_id"])
    return {"ok": True}  # Strava needs a reply within 2 s


def handle_activity(athlete_id: int, activity_id: int, aspect: str) -> None:
    with session() as conn:
        runner = conn.execute(
            "SELECT * FROM runners WHERE strava_athlete_id=? AND active=1", (athlete_id,)).fetchone()
        if runner is None:
            return
        try:
            result = ingest.process_activity(conn, runner, activity_id)
        except Exception:
            log.exception("ingest failed for activity %s", activity_id)
            return
    if result is None or aspect != "create":
        return  # updates refresh stored data quietly
    kind, row = result
    prompt = {
        "run": "A new run just synced. Read the runner summary and notes, get the latest run, and send a short "
               "run debrief with one tip at most (check recent tips first, then record the one you give).",
        "strength": "A new strength session just synced. Get the last strength session and send a short debrief: "
                    "main lifts, change vs last time, one tip at most.",
    }[kind]
    try:
        hermes.run_and_deliver(runner, prompt)
    except Exception:
        log.exception("debrief delivery failed for runner %s", runner["runner_id"])


def handle_deauth(athlete_id: int) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE runners SET strava_access_token=NULL, strava_refresh_token=NULL, strava_expires_at=NULL"
            " WHERE strava_athlete_id=?", (athlete_id,))


# ---- Scheduled jobs (call from cron with JOBS_TOKEN) -----------------------

def _check_jobs_token(request: Request) -> None:
    want = os.environ.get("JOBS_TOKEN", "")
    got = request.headers.get("authorization", "")[7:]
    if not want or not hmac.compare_digest(got, want):
        raise HTTPException(401)


@app.post("/jobs/weekly")
def job_weekly(request: Request, tasks: BackgroundTasks):
    _check_jobs_token(request)
    tasks.add_task(_for_each_runner, "It's the end of the week. Get the week summary and send the weekly "
                   "summary with the mileage check and one focus. Then rewrite the runner summary.")
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
