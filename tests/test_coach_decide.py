"""coach_decide must record the decision and return without waiting for the runner's turn.

Importing app.mcp_server needs `mcp` (and pydantic) from this project's venv. A Hermes
runtime can put its own site-packages ahead of the venv on PYTHONPATH, where pydantic_core
is built for a different Python and fails to import — so drop those entries and retry.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import db as app_db
from app import hermes, queries
from app.admin import add_runner
from app.db import connect, init_db
from app.identity import hash_token


def _import_mcp_server():
    try:
        from app import mcp_server
        return mcp_server
    except ImportError:
        noisy = ("hermes\\installs", "hermes/installs", "hermes-agent\\venv", "hermes-agent/venv")
        sys.path = [p for p in sys.path if not any(n in p for n in noisy)]
        site = Path(sys.executable).parent.parent / "Lib" / "site-packages"
        if site.is_dir() and str(site) not in sys.path:
            sys.path.insert(0, str(site))
        from app import mcp_server
        return mcp_server


mcp_server = _import_mcp_server()

COACH_TOKEN = "coach-token-for-tests-0123456789"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A real database, wired into the module the tools use."""
    path = tmp_path / "runners.db"
    monkeypatch.setattr(app_db, "settings", SimpleNamespace(db_path=str(path)))
    c = connect(str(path))
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def coach_ctx(monkeypatch):
    monkeypatch.setenv("COACH_PROFILE", "coach")
    monkeypatch.setenv("COACH_MCP_TOKEN_HASH", hash_token(COACH_TOKEN))
    return SimpleNamespace(headers={"x-hermes-profile": "coach",
                                   "Authorization": f"Bearer {COACH_TOKEN}"})


@pytest.fixture
def delivered(monkeypatch):
    """Capture what the background thread would send (runner, prompt, thread name)."""
    import threading

    calls = []

    def fake_run_and_deliver(runner, prompt, **kw):
        calls.append((runner, prompt, threading.current_thread().name))
        return "ok"

    monkeypatch.setattr(hermes, "run_and_deliver", fake_run_and_deliver)
    return calls


def _wait_for(calls, n=1, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and len(calls) < n:
        time.sleep(0.01)


# ---- the decision itself ----------------------------------------------------

def test_decide_records_and_returns_before_delivery_finishes(conn, coach_ctx, monkeypatch):
    rid, _, _ = add_runner(conn, "Andrew", "12345", 188, None)
    aid = queries.create_approval(conn, rid, "sore calf", "swap Thursday intervals for an easy run")

    started = []

    def slow_delivery(runner, prompt, **kw):
        started.append(time.perf_counter())
        time.sleep(0.4)  # a real turn is slower than this
        return "ok"

    monkeypatch.setattr(hermes, "run_and_deliver", slow_delivery)

    t0 = time.perf_counter()
    reply = mcp_server.coach_decide(coach_ctx, aid, "approved", "rest up first")
    elapsed = time.perf_counter() - t0

    assert reply == "Recorded: approved. Runner notified."
    assert elapsed < 0.2, f"coach_decide blocked for {elapsed:.2f}s"
    # The decision is already stored when the tool answers.
    assert conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()["status"] == "approved"
    _wait_for(started)
    assert started, "delivery never started"


def test_delivery_runs_on_a_background_thread_and_gets_the_prompt(conn, coach_ctx, delivered):
    import threading

    rid, _, _ = add_runner(conn, "Andrew", "12345", 188, None)
    aid = queries.create_approval(conn, rid, "sore calf", "swap Thursday intervals")
    mcp_server.coach_decide(coach_ctx, aid, "edited", "make it a 40-minute easy run")
    _wait_for(delivered)

    assert delivered, "nothing was delivered"
    runner, prompt, thread_name = delivered[0]
    assert runner["runner_id"] == rid and runner["telegram_chat_id"] == "12345"
    assert runner["hermes_profile"] == f"runner-{rid}"
    assert "edited" in prompt and "make it a 40-minute easy run" in prompt
    assert thread_name != threading.current_thread().name, "delivery did not leave the request thread"


def test_decide_returns_immediately_even_if_delivery_fails(conn, coach_ctx, monkeypatch):
    rid, _, _ = add_runner(conn, "Andrew", "12345", 188, None)
    aid = queries.create_approval(conn, rid, "sore calf", "swap Thursday")

    def boom(runner, prompt, **kw):
        raise RuntimeError("hermes is down")

    monkeypatch.setattr(hermes, "run_and_deliver", boom)
    assert mcp_server.coach_decide(coach_ctx, aid, "rejected", "") == "Recorded: rejected. Runner notified."
    assert conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()["status"] == "rejected"


def test_unknown_id_changes_nothing_and_does_not_notify(conn, coach_ctx, delivered):
    add_runner(conn, "Andrew", "12345", 188, None)
    assert mcp_server.coach_decide(coach_ctx, "made-up-id", "approved") == \
        "No pending request with that id."
    assert delivered == []


def test_only_the_coach_profile_may_decide(conn, coach_ctx, delivered, monkeypatch):
    rid, _, _ = add_runner(conn, "Andrew", "12345", 188, None)
    aid = queries.create_approval(conn, rid, "sore calf", "swap Thursday")
    intruder = SimpleNamespace(headers={"x-hermes-profile": "runner-1",
                                        "Authorization": "Bearer not-the-coach-token-000"})
    with pytest.raises(PermissionError):
        mcp_server.coach_decide(intruder, aid, "approved")
    assert conn.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()["status"] == "pending"
    assert delivered == []


# ---- replies to alerts ------------------------------------------------------

def test_coach_reply_to_the_only_open_alert(conn, coach_ctx, delivered):
    rid, _, _ = add_runner(conn, "Andy", "12345", 184, None)
    aid = queries.create_alert(conn, rid, "other", "Runner asks for a 5 km race time estimate")
    out = mcp_server.coach_reply(coach_ctx, "About 26 minutes, depending on course and weather")
    assert out == f"Sent to Andy (alert #{aid} closed)."
    _wait_for(delivered)
    runner, prompt, _ = delivered[0]
    assert runner["runner_id"] == rid and "About 26 minutes" in prompt and "5 km" in prompt
    assert conn.execute("SELECT handled FROM coach_alerts WHERE id=?", (aid,)).fetchone()[0] == 1


def test_coach_reply_asks_which_when_several_are_open(conn, coach_ctx, delivered):
    a, _, _ = add_runner(conn, "Andy", "1", 184, None)
    b, _, _ = add_runner(conn, "Tom", "2", 190, None)
    queries.create_alert(conn, a, "other", "5k estimate")
    tid = queries.create_alert(conn, b, "injury", "sore calf")
    assert mcp_server.coach_reply(coach_ctx, "rest two days").startswith("More than one alert is open")
    assert delivered == []
    assert mcp_server.coach_reply(coach_ctx, "rest two days", tid) == f"Sent to Tom (alert #{tid} closed)."
    _wait_for(delivered)
    assert delivered[0][0]["runner_id"] == b


def test_only_the_coach_may_reply(conn, coach_ctx, delivered):
    rid, _, _ = add_runner(conn, "Andy", "1", 184, None)
    queries.create_alert(conn, rid, "other", "x")
    intruder = SimpleNamespace(headers={"x-hermes-profile": "runner-1", "Authorization": "Bearer nope-000"})
    with pytest.raises(PermissionError):
        mcp_server.coach_reply(intruder, "hello")
    assert delivered == []
