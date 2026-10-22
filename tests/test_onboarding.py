"""Onboarding, the per-runner record file, focus (runner / lifter / both) and programmes."""
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app import config, ingest, onboarding, plans, programme, queries, record, stats, upload
from app.admin import add_runner
from app.db import connect, init_db
from tests.fitgen import run_fit

UTC = timezone.utc


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


def _runner(conn, rid):
    return conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()


def test_runner_onboarding_in_order(conn):
    rid, _, _ = add_runner(conn, "Tom", "111", None, None)
    st = onboarding.status(conn, rid)
    assert st["next_step"] == "consent" and not st["onboarded"]
    with pytest.raises(ValueError):
        onboarding.finish(conn, rid)  # never without consent
    onboarding.record_consent(conn, rid)
    assert onboarding.status(conn, rid)["next_step"] == "focus"
    onboarding.set_focus(conn, rid, "running")
    info = onboarding.set_watch(conn, rid, "Garmin Forerunner 255")
    assert info["brand"] == "garmin" and "Export Original" in info["how_to_send_files"]
    assert onboarding.status(conn, rid)["next_step"] == "history"
    onboarding.mark(conn, rid, "history: starting fresh")
    aid = onboarding.propose_zones(conn, rid, [132, 144, 152, 162], 184, "screenshot")
    assert onboarding.status(conn, rid)["zones_request"] == "pending"
    soon = (date.today() + timedelta(days=40)).isoformat()
    assert onboarding.save_race(conn, rid, "Durban 10K", soon, 10)["days_to_go"] == 40
    onboarding.mark(conn, rid, "races asked")
    onboarding.mark(conn, rid, "health asked")
    assert onboarding.status(conn, rid)["next_step"] == "finish"
    res = onboarding.finish(conn, rid)
    assert "Durban 10K" in res["coach_summary"] and "waiting for your approval" in res["coach_summary"]
    assert onboarding.status(conn, rid)["onboarded"]
    # Coach approves the zones: they are applied and locked.
    row = queries.decide_approval(conn, aid, "approved")
    assert onboarding.apply_zones(conn, row)
    r = _runner(conn, rid)
    assert json.loads(r["hr_zones"]) == [132, 144, 152, 162] and r["max_hr"] == 184 and r["zones_locked"] == 1


def test_lifter_skips_running_steps(conn):
    rid, _, _ = add_runner(conn, "Lee", "222", None, None)
    onboarding.record_consent(conn, rid)
    onboarding.set_focus(conn, rid, "strength")
    st = onboarding.status(conn, rid)
    assert st["done"]["watch"] and st["done"]["zones"] and st["done"]["races"]
    assert st["next_step"] == "history"
    ingest.store_strength_text(conn, _runner(conn, rid), "Bench 3x5 80kg", date.today().isoformat())
    assert onboarding.status(conn, rid)["next_step"] == "health"
    assert plans.mode(conn, rid)["focus"] == "strength"
    week = queries.week_summary(conn, rid)
    assert week["focus"] == "strength" and week["strength_review"]["last_7_days"]["sessions"] == 1


def test_bad_zones_and_unknown_focus_are_refused(conn):
    rid, _, _ = add_runner(conn, "Tom", "111", None, None)
    with pytest.raises(ValueError):
        onboarding.propose_zones(conn, rid, [150, 140, 152, 162], None, "x")
    with pytest.raises(ValueError):
        onboarding.set_focus(conn, rid, "swimming")


def test_programme_csv_guides_both_disciplines(conn, tmp_path):
    rid, _, _ = add_runner(conn, "Sam", "333", 185, None)
    onboarding.set_focus(conn, rid, "hybrid")
    today = date.today()
    d = lambda n: (today + timedelta(days=n)).isoformat()
    f = tmp_path / "p.csv"
    f.write_text("date,type,description,km,kind,hr_low,hr_high,exercises\n"
                 f"{d(0)},strength,Lower body A,,,,,\"Squat 4x6 70kg; RDL 3x8 60kg\"\n"
                 f"{d(1)},run,Easy run,5,easy,,144,\n"
                 f"{d(3)},run,Hill reps,7,workout,160,175,\n")
    res = programme.load_csv(conn, rid, str(f))
    assert res["run"]["sessions"] == 2 and res["strength"]["sessions"] == 1
    m = plans.mode(conn, rid)
    assert m["running"] == "guided" and m["strength"] == "guided" and m["focus"] == "hybrid"
    with pytest.raises(programme.ProgrammeError):
        programme.load_csv(conn, rid, str(f))  # already active
    s = plans.sessions_around(conn, rid)
    assert s["today"][0]["exercises"] == ["Squat 4x6 70kg", "RDL 3x8 60kg"]
    assert plans.tomorrow_session(conn, rid)[0]["description"] == "Easy run"
    out = ingest.store_strength_text(conn, _runner(conn, rid), "Squat 4x6 70kg", d(0))
    assert out["plan"]["description"] == "Lower body A"
    week = queries.week_summary(conn, rid, queries.week_start(today))
    assert week["strength_vs_programme"]["done"] == 1


def test_splits_from_streams():
    t = list(range(0, 701))
    sp = stats.km_splits(t, [3.0] * len(t), [140] * len(t))
    assert [s["km"] for s in sp] == [1, 2, 3] and sp[0]["pace_s_km"] == pytest.approx(333.3, abs=1)
    assert sp[-1]["distance_m"] == pytest.approx(100, abs=3)


def test_record_file_holds_everything_and_no_secrets(conn, tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setattr(upload, "settings", replace(config.settings, hermes_home=str(home)))
    rid, profile, _ = add_runner(conn, "Tom", "111", 185, None)
    folder = home / "profiles" / profile
    folder.mkdir(parents=True)
    (folder / "r.fit").write_bytes(run_fit(datetime.now(UTC) - timedelta(days=1), seconds=2400, hr=140))
    upload.process_path(conn, _runner(conn, rid), str(folder / "r.fit"))
    ingest.store_strength_text(conn, _runner(conn, rid), "Squat 3x8 60kg", date.today().isoformat())
    queries.log_message(conn, rid, "out", "debrief", "Nice run.", {"run_date": "x"})
    assert not queries.log_message(conn, rid, "out", "debrief", "Nice run.")  # saved once
    queries.add_note(conn, rid, "gear", "Runs in Pegasus 40")

    path = record.export_runner(conn, rid, tmp_path / "tom.db")
    f = sqlite3.connect(path)
    counts = {t: f.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("runs", "run_splits", "strength_sets", "messages", "coach_notes")}
    assert counts == {"runs": 1, "run_splits": 8, "strength_sets": 3, "messages": 1, "coach_notes": 1}
    assert f.execute("SELECT mcp_token_hash, hermes_api_key FROM runners").fetchone() == (None, None)
    f.close()

    # Looking things up
    day = (date.today() - timedelta(days=1)).isoformat()
    splits = record.get_record(conn, rid, "splits", since=day, until=day)
    assert splits["count"] == 8 and splits["items"][0]["km"] == 1
    assert record.get_record(conn, rid, "reports")["items"][0]["basis"] == {"run_date": "x"}
    assert record.get_record(conn, rid, "summary")["runs"]["count"] == 1
    assert "error" in record.get_record(conn, rid, "passwords")


def test_other_runners_never_in_the_file(conn, tmp_path):
    a, _, _ = add_runner(conn, "A", "1", None, None)
    b, _, _ = add_runner(conn, "B", "2", None, None)
    queries.add_note(conn, b, "life", "secret of B")
    f = sqlite3.connect(record.export_runner(conn, a, tmp_path / "a.db"))
    assert f.execute("SELECT COUNT(*) FROM runners").fetchone()[0] == 1
    assert f.execute("SELECT COUNT(*) FROM coach_notes").fetchone()[0] == 0
