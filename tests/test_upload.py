"""File uploads: .fit runs and strength sessions, zips, typed workouts, safety and migration."""
import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app import config, ingest, queries, upload, workout_text
from app.admin import add_runner
from app.db import connect, init_db
from tests.fitgen import run_fit, strength_fit

UTC = timezone.utc


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setattr(upload, "settings", replace(config.settings, hermes_home=str(home)))
    conn = connect(":memory:")
    init_db(conn)
    rid, profile, _ = add_runner(conn, "Tom", "111", 185, "Sub-2 half")
    folder = home / "profiles" / profile
    folder.mkdir(parents=True)
    runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (rid,)).fetchone()
    yield conn, runner, folder, home
    conn.close()


def _save(folder, name, data):
    p = folder / name
    p.write_bytes(data)
    return str(p)


def _start(days_ago=1, hour=4):
    d = datetime.now(UTC).replace(hour=hour, minute=30, second=0, microsecond=0)
    return d - timedelta(days=days_ago)


def test_run_file_is_stored_with_stats(env):
    conn, runner, folder, _ = env
    data = run_fit(_start(), seconds=2400, speed_mps=3.0, hr=140)
    res = upload.process_path(conn, runner, _save(folder, "run.fit", data), note="felt good")
    act = res["activity"]
    assert act["status"] == "stored" and act["kind"] == "run"
    assert act["distance_km"] == 7.2 and act["duration_s"] == 2400 and act["short"] is False
    row = conn.execute("SELECT * FROM runs").fetchone()
    assert row["note"] == "felt good" and row["avg_pace_s_km"] == pytest.approx(333.3, abs=0.1)
    assert row["z2_s"] > 2000  # 140 bpm sits in zone 2 for max HR 185


def test_same_file_twice_is_stored_once(env):
    conn, runner, folder, _ = env
    path = _save(folder, "run.fit", run_fit(_start(), seconds=1500))
    upload.process_path(conn, runner, path)
    again = upload.process_path(conn, runner, path)
    assert again["activity"]["status"] == "duplicate" and again["duplicates"] == 1
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_short_run_flagged(env):
    conn, runner, folder, _ = env
    res = upload.process_path(conn, runner, _save(folder, "s.fit", run_fit(_start(), seconds=600, speed_mps=3)))
    assert res["activity"]["short"] is True  # 1.8 km in 10 min


def test_treadmill_never_counts_for_fitness_check(env):
    conn, runner, folder, _ = env
    upload.process_path(conn, runner, _save(folder, "t.fit",
                        run_fit(_start(), seconds=2400, hr=135, sub_sport="treadmill")))
    row = conn.execute("SELECT treadmill, is_easy_valid, name FROM runs").fetchone()
    assert row["treadmill"] == 1 and row["is_easy_valid"] == 0 and row["name"] == "Treadmill run"


def test_caption_classifies_race(env):
    conn, runner, folder, _ = env
    upload.process_path(conn, runner, _save(folder, "r.fit", run_fit(_start(), seconds=1500, hr=175)),
                        note="parkrun PB!")
    assert conn.execute("SELECT run_type FROM runs").fetchone()[0] == "race"


def test_strength_file_stores_sets(env):
    conn, runner, folder, _ = env
    data = strength_fit(_start(hour=15), [("squat", 8, 60), ("squat", 8, 62.5), ("deadlift", 5, 100)])
    res = upload.process_path(conn, runner, _save(folder, "gym.fit", data))
    assert res["activity"]["kind"] == "strength" and res["activity"]["sets"] == 3
    last = queries.last_strength_session(conn, runner["runner_id"])
    assert [(s["exercise"], s["reps"], s["weight_kg"]) for s in last["sets"]] == [
        ("squat", 8, 60.0), ("squat", 8, 62.5), ("deadlift", 5, 100.0)]


def test_zip_history_import_and_baseline(env):
    conn, runner, folder, _ = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(8):
            z.writestr(f"a{i}.fit", run_fit(_start(days_ago=2 + i * 5), seconds=2700, hr=140))
        z.writestr(f"gym.fit", strength_fit(_start(days_ago=3, hour=16), [("squat", 8, 60)]))
        z.writestr("readme.txt", "ignored")
    res = upload.process_path(conn, runner, _save(folder, "export.zip", buf.getvalue()))
    assert res["history_import"] and res["stored_runs"] == 8 and res["stored_strength"] == 1
    base = queries.baseline(conn, runner["runner_id"])
    assert base["runs"] == 8 and base["enough_data"] and base["longest_run_km"] == 8.1
    assert base["easy_pct"] >= 90 and base["easy_hr"] == 140 and base["strength_sessions"] == 1


def test_path_outside_own_folder_is_refused(env, tmp_path):
    conn, runner, folder, home = env
    outside = tmp_path / "secret.fit"
    outside.write_bytes(run_fit(_start(), seconds=60))
    other = home / "profiles" / "runner-99"
    other.mkdir(parents=True)
    other_file = other / "theirs.fit"
    other_file.write_bytes(run_fit(_start(), seconds=60))
    for bad in (str(outside), str(other_file), str(folder / ".." / "runner-99" / "theirs.fit"),
                str(folder / "missing.fit")):
        with pytest.raises(upload.UploadRejected):
            upload.process_path(conn, runner, bad)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_wrong_type_and_garbage(env):
    conn, runner, folder, _ = env
    with pytest.raises(upload.UploadRejected):
        upload.process_path(conn, runner, _save(folder, "notes.txt", b"hello"))
    res = upload.process_path(conn, runner, _save(folder, "bad.fit", b"not a fit file"))
    assert res["activity"]["status"] == "unreadable"


def test_uploads_refused_when_not_configured(env, monkeypatch):
    conn, runner, folder, _ = env
    monkeypatch.setattr(upload, "settings", replace(config.settings, hermes_home=""))
    with pytest.raises(upload.UploadRejected, match="set up"):
        upload.process_path(conn, runner, _save(folder, "r.fit", run_fit(_start(), seconds=60)))


def test_max_hr_raised_unless_coach_locked(env):
    conn, runner, folder, _ = env
    upload.process_path(conn, runner, _save(folder, "hard.fit",
                        run_fit(_start(), seconds=900, hr=lambda i: 150 + i // 20)))
    r = conn.execute("SELECT max_hr, hr_zones FROM runners").fetchone()
    assert r["max_hr"] == 195 and json.loads(r["hr_zones"])[0] == round(195 * 0.68)
    conn.execute("UPDATE runners SET zones_locked=1, hr_zones='[100,110,120,130]'")
    runner = conn.execute("SELECT * FROM runners").fetchone()
    upload.process_path(conn, runner, _save(folder, "harder.fit",
                        run_fit(_start(days_ago=2), seconds=900, hr=lambda i: 160 + i // 20)))
    r = conn.execute("SELECT max_hr, hr_zones FROM runners").fetchone()
    assert r["max_hr"] == 205 and r["hr_zones"] == "[100,110,120,130]"


def test_typed_workout():
    sets, unparsed = workout_text.parse("Squat 3x8 60kg\nRDL 3 x 10 @ 50 kg; Plank 3x45s\nfelt strong")
    assert [(s.exercise, s.reps, s.weight_kg, s.duration_s) for s in sets[:1]] == [("squat", 8, 60.0, None)]
    assert len(sets) == 9 and sets[3].exercise == "rdl" and sets[3].weight_kg == 50.0
    assert sets[-1].duration_s == 45 and unparsed == ["felt strong"]


def test_typed_workout_stored_once(env):
    conn, runner, _, _ = env
    today = date.today().isoformat()
    first = ingest.store_strength_text(conn, runner, "Squat 3x8 60kg", today)
    assert first["sets"] == 3 and not first["duplicate"]
    assert ingest.store_strength_text(conn, runner, "Squat 3x8 60kg", today)["duplicate"]


def test_migration_from_strava_schema(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE runners (runner_id INTEGER PRIMARY KEY, name TEXT NOT NULL, telegram_chat_id TEXT,
            hermes_profile TEXT UNIQUE NOT NULL, mcp_token_hash TEXT NOT NULL, hermes_api_key TEXT,
            tier TEXT DEFAULT 'free', strava_athlete_id INTEGER, max_hr INTEGER, resting_hr INTEGER,
            hr_zones TEXT, goal TEXT, units TEXT DEFAULT 'km', joined TEXT DEFAULT (date('now')),
            active INTEGER DEFAULT 1);
        CREATE TABLE runs (id INTEGER PRIMARY KEY, runner_id INTEGER NOT NULL, strava_id INTEGER NOT NULL UNIQUE,
            date TEXT NOT NULL, name TEXT, distance_km REAL, duration_s INTEGER, avg_pace_s_km REAL,
            avg_hr REAL, max_hr REAL, z1_s INTEGER, z2_s INTEGER, z3_s INTEGER, z4_s INTEGER, z5_s INTEGER,
            elevation_m REAL, temp_c REAL, hr_drift_pct REAL, run_type TEXT, is_easy_valid INTEGER DEFAULT 0);
        CREATE TABLE strength_sessions (id INTEGER PRIMARY KEY, runner_id INTEGER NOT NULL,
            strava_id INTEGER NOT NULL UNIQUE, date TEXT NOT NULL, title TEXT, duration_s INTEGER,
            avg_hr REAL, raw_description TEXT, parse_ok INTEGER DEFAULT 0);
        CREATE TABLE strength_sets (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL
            REFERENCES strength_sessions(id) ON DELETE CASCADE, runner_id INTEGER NOT NULL, exercise TEXT,
            set_no INTEGER, reps INTEGER, weight_kg REAL, duration_s INTEGER, distance_m REAL);
        CREATE TABLE planned_workouts (id INTEGER PRIMARY KEY, plan_id INTEGER, runner_id INTEGER,
            date TEXT, description TEXT, target TEXT, completed_strava_id INTEGER, status TEXT);
        INSERT INTO runners (runner_id, name, hermes_profile, mcp_token_hash) VALUES (1, 'Tom', 'runner-1', 'x');
        INSERT INTO runs (runner_id, strava_id, date, distance_km) VALUES (1, 555, '2026-10-01', 8.0);
        INSERT INTO strength_sessions (id, runner_id, strava_id, date) VALUES (7, 1, 777, '2026-10-02');
        INSERT INTO strength_sets (session_id, runner_id, exercise) VALUES (7, 1, 'squat');
    """)
    old.commit()
    old.close()
    conn = connect(str(path))
    init_db(conn)
    assert tuple(conn.execute("SELECT activity_key, source FROM runs").fetchone()) == ("strava:555", "strava")
    assert conn.execute("SELECT activity_key FROM strength_sessions").fetchone()[0] == "strava:777"
    assert conn.execute("SELECT zones_locked FROM runners").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # FK from strength_sets still points at the rebuilt table
    conn.execute("DELETE FROM strength_sessions WHERE id=7")
    assert conn.execute("SELECT COUNT(*) FROM strength_sets").fetchone()[0] == 0
    init_db(conn)  # running again is harmless
    conn.close()
