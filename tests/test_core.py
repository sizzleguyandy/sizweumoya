from datetime import date, timedelta

import pytest

from app import queries, stats
from app.admin import add_runner
from app.db import connect, init_db
from app.hermes import extract_text
from app.identity import AccessDenied, resolve_runner


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


# ---- Stats ------------------------------------------------------------------

def test_time_in_zones_and_classification():
    zones = [130, 145, 160, 172]
    t = list(range(0, 601))
    hr = [140] * 601
    zone_s = stats.time_in_zones(t, hr, zones)
    assert zone_s == [0, 600, 0, 0, 0]
    assert stats.classify_run("Morning Run", None, 8, zone_s) == "easy"
    assert stats.classify_run("Intervals 6x800", None, 8, zone_s) == "workout"
    assert stats.classify_run("Sunday", 2, 12, zone_s) == "long"


def test_hr_drift_detects_rise():
    n = 200
    t, v = list(range(n)), [3.0] * n
    hr = [140] * (n // 2) + [150] * (n // 2)
    assert stats.hr_drift_pct(t, hr, v) == pytest.approx(7.1, abs=0.1)


def test_mileage_verdicts():
    assert stats.mileage_verdict(stats.km_change_pct(32, 28)) == "big_jump"
    assert stats.mileage_verdict(stats.km_change_pct(30, 28)) == "steady"
    assert stats.mileage_verdict(stats.km_change_pct(20, 28)) == "lighter"
    assert stats.mileage_verdict(stats.km_change_pct(20, 0)) == "no_baseline"


def test_fitness_check():
    prev = [{"avg_pace_s_km": 378, "avg_hr": 140}] * 3
    now = [{"avg_pace_s_km": 365, "avg_hr": 140}] * 3
    assert stats.fitness_check(now, prev).verdict == "fitter"
    assert stats.fitness_check(prev, prev).verdict == "steady"
    assert stats.fitness_check(prev, now).verdict == "dipping"
    assert stats.fitness_check(now[:2], prev).verdict == "insufficient"


# ---- Identity: the model can never reach another runner ----------------------

def test_identity_requires_matching_profile_and_token(conn):
    rid_a, prof_a, tok_a = add_runner(conn, "A", "111", 188, None)
    rid_b, prof_b, tok_b = add_runner(conn, "B", "222", 190, None)
    h = "x-hermes-profile"
    ok = resolve_runner(conn, {h: prof_a, "Authorization": f"Bearer {tok_a}"}, h)
    assert ok["runner_id"] == rid_a
    with pytest.raises(AccessDenied):  # A's token with B's profile name
        resolve_runner(conn, {h: prof_b, "Authorization": f"Bearer {tok_a}"}, h)
    with pytest.raises(AccessDenied):
        resolve_runner(conn, {h: prof_a}, h)
    with pytest.raises(AccessDenied):
        resolve_runner(conn, None, h)


def test_queries_are_scoped_to_runner(conn):
    rid_a, _, _ = add_runner(conn, "A", "111", 188, None)
    rid_b, _, _ = add_runner(conn, "B", "222", 190, None)
    today = date.today().isoformat()
    conn.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, avg_pace_s_km, run_type)"
                 " VALUES (?,?,?,?,?,?)", (rid_a, "fit:1", today, 8.0, 340, "easy"))
    note_id = queries.add_note(conn, rid_a, "health", "Tight left calf")
    assert len(queries.recent_runs(conn, rid_a)) == 1
    assert queries.recent_runs(conn, rid_b) == []
    assert queries.notes_and_summary(conn, rid_b)["active_notes"] == []
    assert queries.set_note_status(conn, rid_b, note_id, "resolved") is False  # can't touch A's note


def test_week_summary_and_approval_flow(conn):
    rid, _, _ = add_runner(conn, "A", "111", 188, None)
    monday = queries.week_start(date.today())
    for i, km in enumerate([10, 12]):
        conn.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, z2_s, z1_s, z3_s, z4_s, z5_s)"
                     " VALUES (?,?,?,?,?,0,0,0,0)", (rid, f"fit:{10 + i}", (monday - timedelta(days=7)).isoformat(), km, 100))
    conn.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, z2_s, z1_s, z3_s, z4_s, z5_s)"
                 " VALUES (?,?,?,?,?,0,0,0,0)", (rid, "fit:20", monday.isoformat(), 26, 100))
    w = queries.week_summary(conn, rid, monday)
    assert w["total_km"] == 26 and w["last_week_km"] == 22 and w["mileage_verdict"] == "big_jump"
    assert "volume_jump" in w["tips"]

    aid = queries.create_approval(conn, rid, "missed Tuesday", "move intervals to Thursday")
    assert queries.decide_approval(conn, "made-up-id", "approved") is None
    assert queries.decide_approval(conn, aid, "approved")["status"] == "approved"
    assert queries.decide_approval(conn, aid, "rejected") is None  # already decided


def test_extract_text_shapes():
    assert extract_text({"output_text": "hi"}) == "hi"
    assert extract_text({"output": [{"content": [{"type": "output_text", "text": "a"}]}]}) == "a"
    assert extract_text({}) == ""
