"""Dex, the run brain: programme drafts from the TR3D Run Coach engine, approved by Coach Andy."""
import json
from datetime import date, timedelta

import pytest

from app import dex, programme, queries
from app.admin import add_runner
from app.db import connect, init_db

TODAY = date(2026, 10, 27)          # a Tuesday


@pytest.fixture
def db():
    c = connect(":memory:")
    init_db(c)
    rid, _, _ = add_runner(c, "Tom", "111", 186, None)
    c.execute("UPDATE runners SET hr_zones='[132,144,152,162]', easy_hr_cap=144 WHERE runner_id=?", (rid,))
    for i in range(18):                                   # six weeks of ~25 km
        d = (TODAY - timedelta(days=2 + i * 2)).isoformat()
        c.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, duration_s, run_type)"
                  " VALUES (?,?,?,?,?,?)", (rid, f"fit:{i}", d, 8.0 if i % 3 == 0 else 7.0, 2700, "easy"))
    c.execute("INSERT INTO runs (runner_id, activity_key, date, distance_km, duration_s, run_type)"
              " VALUES (?, 'fit:tt', ?, 5.0, 1500, 'benchmark')", (rid, (TODAY - timedelta(days=10)).isoformat()))
    c.execute("INSERT INTO races (runner_id, name, date, distance_km, ascent_m) VALUES (?,?,?,?,?)",
              (rid, "Grape Run", "2027-01-17", 21.1, 445))
    c.commit()
    yield c, rid
    c.close()


def test_draft_lands_race_on_race_day_with_hr_targets(db):
    c, rid = db
    d = dex.draft(c, rid, extra_days="Thu,Sat", today=TODAY)
    rows = d["rows"]
    assert d["summary"]["start"] == "2026-11-02" and d["summary"]["weeks"] == 11
    race = rows[-1]
    assert race["kind"] == "race" and race["date"] == "2027-01-17" and race["km"] == "21.1" and race["climb_m"] == "445"
    days = {date.fromisoformat(r["date"]).strftime("%a") for r in rows}
    assert days <= {"Tue", "Thu", "Sat", "Sun", "Wed", "Fri"}            # race-week shake-outs aside
    assert all(r["kind"] in ("easy", "long", "workout", "race") for r in rows)
    thr = [r for r in rows if r["description"].startswith("Threshold")]
    assert thr and all((r["hr_low"], r["hr_high"]) == ("153", "162") for r in thr)
    assert all(not r["hr_low"] for r in rows if r["kind"] in ("easy", "long"))   # easy cap comes from the profile
    assert d["inputs"]["vdot_from"].startswith("5.0 km")
    assert "VDOT" in dex.summary_text("Tom", d) and dex.week_lines(d)[0].startswith("W1 Tue 03 Nov")


def test_fewer_than_six_weeks_is_refused(db):
    c, rid = db
    c.execute("UPDATE races SET date='2026-11-15'")
    with pytest.raises(dex.DexError, match="at least 6"):
        dex.draft(c, rid, today=TODAY)


def test_needs_zones_and_a_benchmark(db):
    c, rid = db
    c.execute("DELETE FROM runs WHERE run_type='benchmark'")
    with pytest.raises(dex.DexError, match="time trial"):
        dex.draft(c, rid, today=TODAY)
    assert dex.draft(c, rid, vdot=45, today=TODAY)["inputs"]["vdot_from"] == "given by Coach Andy"
    c.execute("UPDATE runners SET hr_zones=NULL")
    with pytest.raises(dex.DexError, match="zones"):
        dex.draft(c, rid, vdot=45, today=TODAY)


def test_bad_days_are_refused(db):
    c, rid = db
    with pytest.raises(dex.DexError, match="different days"):
        dex.draft(c, rid, long_day="Sun", extra_days="Sun", today=TODAY)
    with pytest.raises(dex.DexError, match="isn't a day"):
        dex.draft(c, rid, quality_day="Funday", today=TODAY)


def test_nothing_loads_until_approved_then_programme_replaces_old(db, tmp_path, monkeypatch):
    c, rid = db
    monkeypatch.setattr(dex, "drafts_dir", lambda: tmp_path)
    old = programme.load_rows(c, rid, [{"date": "2026-11-03", "type": "run", "description": "Old easy",
                                        "km": "5", "kind": "easy"}])
    d = dex.draft(c, rid, today=TODAY)
    p = dex.propose(c, rid, d)
    ap = dict(c.execute("SELECT * FROM approvals WHERE id=?", (p["approval_id"],)).fetchone())
    assert ap["kind"] == "programme" and ap["status"] == "pending"
    assert c.execute("SELECT COUNT(*) FROM plans WHERE active=1").fetchone()[0] == 1      # still the old one

    # The file is the programme template, and loads back to the same sessions.
    rows_, _ = programme.read_rows(p["file"])
    assert len(rows_) == len(d["rows"]) and rows_[-1]["kind"] == "race" and rows_[0]["week"] == "1"

    row = queries.decide_approval(c, p["approval_id"], "approved")
    out = dex.apply(c, row)
    assert out["sessions"] == len(d["rows"]) and out["to"] == "2027-01-17"
    active = c.execute("SELECT id FROM plans WHERE active=1 AND type='run'").fetchall()
    assert len(active) == 1 and active[0][0] != old["run"]["plan_id"]
    t = json.loads(c.execute("SELECT target FROM planned_workouts WHERE plan_id=? ORDER BY date LIMIT 1",
                             (active[0][0],)).fetchone()[0])
    assert t["week"] == 1 and t["phase"] == "Base"
    assert dex.apply(c, {"kind": "move", "payload": "{}"}) is None
