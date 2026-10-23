"""Hevy webhook: who it's for, fetch, store, edits, warm-ups, and the 5-second rule."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app import hermes, hevy, main, queries
from app.admin import add_runner
from app.db import connect, init_db

WORKOUT = {
    "id": "f1085cdb-32b2-4003-967d-53a3af8eaecb", "title": "Upper A", "description": "felt strong",
    "start_time": "2026-10-21T15:00:00+02:00", "end_time": "2026-10-21T16:05:00+02:00",
    "updated_at": "2026-10-21T16:06:00Z",
    "exercises": [
        {"title": "Chest Press (Machine)", "sets": [
            {"type": "warmup", "weight_kg": 20, "reps": 10},
            {"type": "normal", "weight_kg": 35, "reps": 5},
            {"type": "normal", "weight_kg": 35, "reps": 7, "rpe": 8}]},
        {"title": "Plank", "sets": [{"type": "normal", "duration_seconds": 45}]},
    ],
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "runners.db"
    monkeypatch.setattr(app_db, "settings", SimpleNamespace(db_path=str(path)))
    c = connect(str(path))
    init_db(c)
    rid, _, _ = add_runner(c, "Andy", "111", 186, None)
    c.execute("UPDATE runners SET tier='full'")
    c.commit()
    yield c, rid
    c.close()


def test_parse_and_store_skips_warmups_and_handles_edits(db):
    c, rid = db
    r = c.execute("SELECT * FROM runners").fetchone()
    res = hevy.store(c, r, WORKOUT)
    assert res == {"status": "stored", "replaced_manual": 0, "date": "2026-10-21", "title": "Upper A", "sets": 3,
                   "exercises": 2, "plan": None}
    last = queries.last_strength_session(c, rid)
    assert [(s["exercise"], s["reps"], s["weight_kg"]) for s in last["sets"]][:2] == [
        ("chest press (machine)", 5, 35.0), ("chest press (machine)", 7, 35.0)]
    assert last["duration_s"] == 3900
    assert hevy.store(c, r, WORKOUT)["status"] == "duplicate"
    edited = dict(WORKOUT, updated_at="2026-10-22T08:00:00Z",
                  exercises=[{"title": "Chest Press (Machine)", "sets": [{"type": "normal", "weight_kg": 37.5, "reps": 5}]}])
    assert hevy.store(c, r, edited)["status"] == "updated"
    assert c.execute("SELECT COUNT(*) FROM strength_sessions").fetchone()[0] == 1
    assert c.execute("SELECT weight_kg FROM strength_sets").fetchall()[0][0] == 37.5


def test_webhook_checks_secret_and_answers_fast(db, monkeypatch):
    c, rid = db
    secret = hevy.connect(c, rid, "a-real-looking-hevy-api-key-0001")
    assert c.execute("SELECT secret_hash FROM hevy_links").fetchone()[0] != secret  # only the hash is kept
    fetched, delivered = [], []
    monkeypatch.setattr(hevy, "fetch_workout", lambda key, wid: fetched.append((key, wid)) or WORKOUT)
    monkeypatch.setattr(hermes, "run_and_deliver", lambda r, p, **kw: delivered.append((r["runner_id"], kw)))
    c.execute("UPDATE runners SET onboarded_at='2026-01-01', hermes_api_key='k'")
    c.commit()
    client = TestClient(main.app)
    body = {"workoutId": WORKOUT["id"]}
    assert client.post("/hevy/webhook", json=body).status_code == 401
    assert client.post("/hevy/webhook", json=body, headers={"Authorization": "Bearer wrong-secret-0123456789"}).status_code == 401
    ok = client.post("/hevy/webhook", json=body, headers={"Authorization": f"Bearer {secret}"})
    assert ok.status_code == 200
    assert fetched == [("a-real-looking-hevy-api-key-0001", WORKOUT["id"])]
    assert c.execute("SELECT source FROM strength_sessions").fetchone()[0] == "hevy"
    assert delivered == [(rid, {"kind": "strength_debrief"})]
    bad = client.post("/hevy/webhook", json={"workoutId": "../../etc"}, headers={"Authorization": f"Bearer {secret}"})
    assert bad.status_code == 400


def test_api_key_never_in_the_record_file(db, tmp_path):
    from app import record
    import sqlite3
    c, rid = db
    hevy.connect(c, rid, "a-real-looking-hevy-api-key-0001")
    f = sqlite3.connect(record.export_runner(c, rid, tmp_path / "a.db"))
    tables = {r[0] for r in f.execute("SELECT name FROM sqlite_master")}
    assert "hevy_links" not in tables


def test_free_tier_cannot_connect(db):
    c, rid = db
    c.execute("UPDATE runners SET tier='free'")
    c.commit()
    with pytest.raises(hevy.HevyError, match="paid"):
        hevy.connect(c, rid, "a-real-looking-hevy-api-key-0001")


def test_hevy_replaces_the_same_day_typed_or_sheet_session(db):
    from app import ingest
    c, rid = db
    r = c.execute("SELECT * FROM runners").fetchone()
    ingest.store_strength_text(c, r, "Chest press 5x5 35kg", "2026-10-21")
    ingest.store_strength_text(c, r, "Squat 3x5 80kg", "2026-10-19")
    res = hevy.store(c, r, WORKOUT)
    assert res["replaced_manual"] == 1
    assert sorted(x[0] for x in c.execute("SELECT source FROM strength_sessions")) == ["hevy", "text"]
    assert hevy.store(c, r, WORKOUT)["status"] == "duplicate"   # and no further deletions
