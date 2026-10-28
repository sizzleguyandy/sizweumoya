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
    assert mcp_server.coach_decide(coach_ctx, "made-up-id", "approved").startswith("No request with that id exists")
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


# ---- programme changes ------------------------------------------------------

def _plan(conn, rid):
    import json as _j
    pid = conn.execute("INSERT INTO plans (runner_id, type, start_date, end_date) VALUES (?,?,?,?)",
                       (rid, "run", "2026-10-20", "2026-11-15")).lastrowid
    for d, desc in (("2026-10-23", "Easy run"), ("2026-10-25", "Long run")):
        conn.execute("INSERT INTO planned_workouts (plan_id, runner_id, date, description, target) VALUES (?,?,?,?,?)",
                     (pid, rid, d, desc, _j.dumps({"kind": "easy", "km": 5, "day": "Fri"})))
    conn.commit()


def test_approved_move_updates_the_programme(conn, coach_ctx, delivered, monkeypatch):
    rid, profile, token = add_runner(conn, "Andy", "12345", 186, None)
    _plan(conn, rid)
    monkeypatch.setattr(hermes, "notify_coach", lambda text: None)
    ctx = SimpleNamespace(headers={"x-hermes-profile": profile, "Authorization": f"Bearer {token}"})
    msg = mcp_server.propose_plan_change(ctx, "couldn't run today", "move my easy run", "2026-10-23", "2026-10-24")
    aid = msg.split("request ")[1].rstrip(").")
    out = mcp_server.coach_decide(coach_ctx, aid, "approved")
    assert "Programme updated" in out and "Saturday 2026-10-24" in out
    assert [r[0] for r in conn.execute("SELECT date FROM planned_workouts ORDER BY date")] == ["2026-10-24", "2026-10-25"]
    _wait_for(delivered)
    assert "Programme updated: moved to Saturday 2026-10-24" in delivered[0][1]


def test_rejected_move_changes_nothing(conn, coach_ctx, delivered, monkeypatch):
    rid, profile, token = add_runner(conn, "Andy", "12345", 186, None)
    _plan(conn, rid)
    monkeypatch.setattr(hermes, "notify_coach", lambda text: None)
    ctx = SimpleNamespace(headers={"x-hermes-profile": profile, "Authorization": f"Bearer {token}"})
    aid = mcp_server.propose_plan_change(ctx, "r", "p", "2026-10-23", "2026-10-24").split("request ")[1].rstrip(").")
    mcp_server.coach_decide(coach_ctx, aid, "rejected")
    assert conn.execute("SELECT date FROM planned_workouts ORDER BY date").fetchone()[0] == "2026-10-23"


def test_coach_can_see_and_move_a_runners_plan(conn, coach_ctx, delivered):
    rid, _, _ = add_runner(conn, "Andy", "12345", 186, None)
    _plan(conn, rid)
    assert "error" in mcp_server.coach_runner_plan(coach_ctx, "nobody")
    out = mcp_server.coach_move_session(coach_ctx, "andy", "2026-10-23", "2026-10-24")
    assert out.startswith("Moved Easy run from Friday 2026-10-23 to Saturday 2026-10-24")
    assert mcp_server.coach_move_session(coach_ctx, "andy", "2026-10-23", "2026-10-24").startswith("Not moved")
    intruder = SimpleNamespace(headers={"x-hermes-profile": "runner-1", "Authorization": "Bearer nope-000"})
    with pytest.raises(PermissionError):
        mcp_server.coach_move_session(intruder, "andy", "2026-10-24", "2026-10-26")


def test_dex_draft_waits_for_approval_then_loads(conn, coach_ctx, delivered, monkeypatch, tmp_path):
    from datetime import date, timedelta

    from app import dex
    rid, _, _ = add_runner(conn, "Tom", "12345", 186, None)
    today = date.today()
    conn.execute("UPDATE runners SET hr_zones='[132,144,152,162]', easy_hr_cap=144")
    for i in range(12):
        conn.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, duration_s, run_type)"
                     " VALUES (?,?,?,?,?,?)", (rid, f"fit:{i}", (today - timedelta(days=2 + 3 * i)).isoformat(),
                                               7.0, 2700, "benchmark" if i == 0 else "easy"))
    conn.execute("INSERT INTO races (runner_id, name, date, distance_km) VALUES (?,?,?,?)",
                 (rid, "City 10K", (today + timedelta(days=70)).isoformat(), 10))
    conn.commit()
    sent = []
    monkeypatch.setattr(hermes, "send_telegram_file", lambda chat, path, caption="": sent.append(path))
    monkeypatch.setattr(dex, "drafts_dir", lambda: tmp_path)
    assert mcp_server.coach_dex_draft(coach_ctx, "nobody").startswith("Dex couldn't draft it")
    out = mcp_server.coach_dex_draft(coach_ctx, "tom", extra_days="Thu,Sat")
    assert out.startswith("Dex programme draft for Tom: City 10K") and "First two weeks:" in out and sent
    assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0          # nothing loaded yet
    aid = out.rsplit("reject ", 1)[1].split(".")[0]
    res = mcp_server.coach_decide(coach_ctx, aid, "approved")
    assert "Programme loaded" in res
    assert conn.execute("SELECT COUNT(*) FROM plans WHERE active=1 AND type='run'").fetchone()[0] == 1
    _wait_for(delivered)
    assert "Your new programme is in" in delivered[0][1]


def test_deciding_twice_says_what_happened(conn, coach_ctx, delivered, monkeypatch):
    rid, profile, token = add_runner(conn, "Andy", "12345", 186, None)
    _plan(conn, rid)
    monkeypatch.setattr(hermes, "notify_coach", lambda text: None)
    ctx = SimpleNamespace(headers={"x-hermes-profile": profile, "Authorization": f"Bearer {token}"})
    aid = mcp_server.propose_plan_change(ctx, "r", "p").split("request ")[1].rstrip(").")
    mcp_server.coach_decide(coach_ctx, aid, "rejected", "test clean-up")
    again = mcp_server.coach_decide(coach_ctx, aid, "approved")
    assert again.startswith(f"Nothing changed: request {aid}") and "already rejected" in again and "test clean-up" in again
    assert mcp_server.coach_decide(coach_ctx, "nope", "approved").startswith("No request with that id")
    plan = mcp_server.coach_runner_plan(coach_ctx, "andy")
    assert plan["active_programmes"][0]["start_date"] == "2026-10-20" and plan["races"] == []


def test_coach_edits_a_session_and_can_make_it_rest(conn, coach_ctx, delivered):
    import json as _j
    from datetime import date as _d
    from app import plans
    rid, _, _ = add_runner(conn, "Andy", "12345", 186, None)
    _plan(conn, rid)
    out = mcp_server.coach_edit_session(coach_ctx, "andy", "2026-10-25", km=8, kind="long",
                                        description="Long run, shortened", hr_high=144)
    assert out.startswith("Changed Sunday 2026-10-25: Long run -> Long run, shortened, 8 km, HR under 144")
    t = _j.loads(conn.execute("SELECT target FROM planned_workouts WHERE date='2026-10-25'").fetchone()[0])
    assert t["km"] == 8 and t["kind"] == "long" and t["hr_max"] == 144
    assert mcp_server.coach_edit_session(coach_ctx, "andy", "2026-10-23", rest=True).endswith("runner notified.")
    assert conn.execute("SELECT status FROM planned_workouts WHERE date='2026-10-23'").fetchone()[0] == "cancelled"
    vs = plans.week_vs_programme(conn, rid, _d(2026, 10, 19), _d(2026, 10, 26), {"total_km": 0})
    assert vs["sessions_planned"] == 1 and vs["planned_km"] == 8          # the rest day no longer counts
    assert mcp_server.coach_edit_session(coach_ctx, "andy", "2026-10-23", km=5).startswith("Not changed")
    assert mcp_server.coach_edit_session(coach_ctx, "andy", "2026-10-25", kind="sprint").startswith("Not changed")
    _wait_for(delivered, 2)
    assert "changed your session on Sunday 2026-10-25" in delivered[0][1]
