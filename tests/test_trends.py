"""Trends without a programme: run context, personal bests, progress, load flags, taper, records."""
from datetime import date, timedelta

import pytest

from app import ingest, queries, trends
from app.admin import add_runner
from app.db import connect, init_db

TODAY = date(2026, 10, 22)   # a Thursday
MON = TODAY - timedelta(days=TODAY.weekday())


@pytest.fixture
def db():
    c = connect(":memory:")
    init_db(c)
    rid, _, _ = add_runner(c, "Tom", "1", 185, None)
    yield c, rid
    c.close()


def run(c, rid, d, km, pace=360, hr=140, rtype="easy", valid=1, z_easy=None, z_hard=0):
    secs = int(km * pace)
    c.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, duration_s, avg_pace_s_km, avg_hr,"
              " run_type, is_easy_valid, z2_s, z4_s) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (rid, f"k{d}{km}{pace}", d.isoformat(), km, secs, pace, hr, rtype, valid,
               secs if z_easy is None else z_easy, z_hard))
    c.commit()


def test_run_context_compares_and_finds_personal_bests(db):
    c, rid = db
    for i in range(3):
        run(c, rid, TODAY - timedelta(days=7 * (i + 1)), 8.0, pace=380, hr=142)
    ctx = trends.run_context(c, rid, TODAY.isoformat(), 8.2, 365, 141, "easy")
    assert ctx["vs_similar"]["pace_diff_s_km"] == -15 and ctx["vs_similar"]["their_usual_hr"] == 142
    assert {p["type"] for p in ctx["personal_bests"]} == {"longest_run", "fastest_5k_plus"}
    assert "vs_similar" not in trends.run_context(c, rid, TODAY.isoformat(), 8.0, 300, 150, "easy", treadmill=True)


def test_load_flags_green_amber_red(db):
    c, rid = db
    for w in range(1, 5):                              # four steady 20 km weeks
        for d in (0, 2, 5):
            run(c, rid, MON - timedelta(weeks=w) + timedelta(days=d), 20 / 3)
    run(c, rid, MON, 7)
    assert trends.load(c, rid, MON)["level"] == "green"
    run(c, rid, MON + timedelta(days=1), 10)
    run(c, rid, MON + timedelta(days=2), 9, z_easy=0, z_hard=9 * 360)   # 26 km, a hard run
    flags = trends.load(c, rid, MON)
    assert flags["level"] in ("amber", "red") and any("up" in s for s in flags["signs"])
    queries.add_note(c, rid, "health", "sore calf")
    assert trends.load(c, rid, MON)["level"] == "red" and trends.load(c, rid, MON)["careful_mode"]


def test_return_after_a_down_week_is_not_flagged(db):
    c, rid = db
    for w, km in ((4, 20), (3, 20), (2, 20), (1, 8)):    # down week last week
        run(c, rid, MON - timedelta(weeks=w), km)
    run(c, rid, MON, 20)
    assert trends.load(c, rid, MON)["level"] == "green"


def test_progress_and_stagnation(db):
    c, rid = db
    for w in range(1, 9):   # 8 weeks, distance and long run growing
        wk = MON - timedelta(weeks=w)
        run(c, rid, wk, 5 + (8 - w))
        run(c, rid, wk + timedelta(days=3), 5)
    p = trends.progress(c, rid, TODAY)
    assert p["signals"]["weekly_km"]["trend"] == "rising" and p["status"] == "green"
    assert p["signals"]["longest_run"]["trend"] == "rising"
    assert c.execute("SELECT COUNT(*) FROM fitness_checks").fetchone()[0] == 0  # progress doesn't save checks


def test_taper_for_a_half(db):
    c, rid = db
    for w in range(1, 4):
        run(c, rid, MON - timedelta(weeks=w), 40)
    c.execute("INSERT INTO races (runner_id, name, date, distance_km) VALUES (?,?,?,?)",
              (rid, "Grape Run", (TODAY + timedelta(days=24)).isoformat(), 21.1))
    c.commit()
    t = trends.taper(c, rid, TODAY)
    assert t["race_type"] == "half" and t["base_km_per_week"] == 40
    assert [p["target_km"] for p in t["plan"]] == [30, 20]
    c.execute("UPDATE races SET date=?", ((TODAY + timedelta(days=60)).isoformat(),))
    assert trends.taper(c, rid, TODAY) is None


def test_strength_records(db):
    c, rid = db
    r = c.execute("SELECT * FROM runners").fetchone()
    old = (date.today() - timedelta(days=60)).isoformat()
    ingest.store_strength_text(c, r, "Squat 3x5 80kg", old)
    ingest.store_strength_text(c, r, "Squat 3x5 85kg", date.today().isoformat())
    rec = trends.strength_records(c, rid)[0]
    assert rec["best_kg"] == 85 and rec["new_record"] and rec["est_1rm_kg"] == pytest.approx(99.2, abs=0.1)


def test_red_flag_goes_to_coach_amber_does_not(db, monkeypatch):
    from app import hermes, main
    c, rid = db
    c.execute("UPDATE runners SET onboarded_at='2026-01-01'")
    c.commit()
    sent = []
    monkeypatch.setattr(hermes, "notify_coach", lambda text: sent.append(text))
    for w in range(1, 5):
        run(c, rid, MON - timedelta(weeks=w), 20)
    run(c, rid, MON, 7)
    assert main.escalate_load(c, TODAY) == [] and sent == []
    queries.add_note(c, rid, "health", "sore calf")
    run(c, rid, MON + timedelta(days=1), 25)
    assert main.escalate_load(c, TODAY) == ["Tom"] and "Red load flag" in sent[0]
    assert c.execute("SELECT flag_level FROM weekly_summary").fetchone()[0] == "red"


def test_week_card(db):
    from app.queries import week_card
    w = {"focus": "hybrid", "total_km": 14.3, "last_week_km": 20, "runs": 2, "total_time": "1:25:47",
         "easy_pct": 81, "time_in_zones": [{"minutes": 70}], "strength_sessions": 1,
         "strength_review": {"last_7_days": {"by_muscle": {"quads": {"sets": 3}, "chest": {"sets": 3}}}},
         "load": {"level": "green"}}
    card = week_card(w, MON, today=TODAY)
    assert card.splitlines() == [
        "MY WEEK  19 Oct - 25 Oct  (day 4 of 7)",
        "Run       14.3 km  ▰▰▰▰▰▰▰▱▱▱  last week 20 km",
        "          2 runs · 1:25:47",
        "Easy      81% of running time",
        "Strength  1 session · 6 sets",
        "Load      Green (all good)",
    ]
    w2 = {"focus": "running", "total_km": 20, "runs": 3, "total_time": "2:00:00", "easy_pct": 80,
          "time_in_zones": [{"minutes": 100}], "strength_sessions": 0,
          "vs_programme": {"planned_km": 33, "done": 2, "sessions_planned": 4}}
    assert "of 33 km planned" in week_card(w2, MON, today=TODAY) and "2/4 sessions done" in week_card(w2, MON, today=TODAY)
    lifter = week_card({"focus": "strength", "strength_sessions": 2}, MON, today=TODAY)
    assert "Run" not in lifter and "Strength  2 sessions" in lifter
