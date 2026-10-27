"""Programme form (Google Form CSV) -> Dex; race library; Couch to 5K; race-day pacing plan."""
import csv
from datetime import date, datetime, timedelta

import httpx
import pytest

from app import dex, intake, racelib, raceplan, truepace
from app.admin import add_runner
from app.db import connect, init_db

TODAY = date.today()
T = {k: t for k, t, *_ in intake.QUESTIONS}


@pytest.fixture
def db():
    c = connect(":memory:")
    init_db(c)
    rid, _, _ = add_runner(c, "Tom", "111", 186, None)
    c.execute("UPDATE runners SET hr_zones='[132,144,152,162]', easy_hr_cap=144")
    c.commit()
    yield c, rid, intake.code_for(c, rid)
    c.close()


def _row(code, **over):
    race_day = TODAY + timedelta(weeks=14)
    base = {"Timestamp": "2026/10/27 10:00:00", T["code"]: code.lower(), T["name"]: "Tom", T["goal"]: "A race",
            T["race"]: "Sanlam Cape Town Marathon", T["race_date"]: race_day.strftime("%d/%m/%Y"),
            T["recent"]: "10 km", T["recent_time"]: "50:03", T["recent_date"]: "",
            T["weekly_km"]: "40", T["longest_km"]: "18",
            T["run_days"]: "Tuesday, Thursday, Saturday, Sunday", T["long_day"]: "Sunday",
            T["quality_day"]: "Tuesday", T["build"]: "Faster (experienced, consistent runners)",
            T["race_pacing"]: "Yes", T["location"]: "", T["injuries"]: "sore left Achilles in August",
            T["notes"]: ""}
    base.update({T[k]: v for k, v in over.items()})
    return base


def test_library_has_the_races_and_hides_unreviewed_tips():
    names = {r["id"] for r in racelib.all_races()}
    assert {"cape_town_marathon", "two_oceans_marathon", "comrades_marathon", "london_marathon"} <= names
    assert [r["id"] for r in racelib.find("two oceans")] == ["two_oceans_marathon"]
    assert racelib.find("Grape Run") == []
    g = racelib.guide("cape_town_marathon")
    assert "## The Course" in g and "Tips for TR3D" not in g and "Threshold (T) pace" not in g
    assert "Goal marathon pace" in racelib.pacing("cape_town_marathon", 45)


def test_form_import_saves_race_health_and_drives_dex(db):
    c, rid, code = db
    res = intake.import_rows(c, [_row(code)])
    assert res[0]["status"] == "imported" and res[0]["goal"] == "race"
    race = c.execute("SELECT name, distance_km, ascent_m, preset_id FROM races").fetchone()
    assert tuple(race) == ("Sanlam Cape Town Marathon", 42.195, 282, "cape_town_marathon")
    assert c.execute("SELECT note FROM coach_notes WHERE category='health'").fetchone()[0].endswith("August")
    assert intake.import_rows(c, [_row(code)])[0]["status"] == "already imported"
    d = dex.draft(c, rid)                       # no runs on file: everything from the form
    i, s = d["inputs"], d["summary"]
    assert i["weekly_km"] == 40 and i["weekly_km_from"] == "their programme form"
    assert 39.5 <= i["vdot"] <= 40.5 and "programme form" in i["vdot_from"]
    assert s["profile"] == "aggressive" and s["days"] == {"long": "Sun", "quality": "Tue", "extra": ["Thu", "Sat"]}
    assert s["hilliness"] == "medium" and s["race_pacing_plan"] is True
    assert any("Achilles" in w for w in d["warnings"])
    assert d["rows"][-1]["kind"] == "race" and d["rows"][-1]["climb_m"] == "282"
    assert "race-day pacing plan: yes" in dex.summary_text("Tom", d)


def test_bad_answers_are_reported_not_guessed(db):
    c, rid, code = db
    out = intake.import_rows(c, [_row("TR3D-NOPE1"), _row(code, quality_day="Sunday"),
                                 _row(code, recent_time="fast"), _row(code, race="Another race (not in this list)")])
    assert [r["status"] for r in out] == ["error"] * 4
    assert "no runner has the code" in out[0]["error"] and "different days" in out[1]["error"]
    assert "finish time" in out[2]["error"] and "name, distance and date" in out[3]["error"]
    assert c.execute("SELECT COUNT(*) FROM dex_intake").fetchone()[0] == 0


def test_csv_headers_are_the_question_titles(tmp_path, db):
    c, rid, code = db
    f = tmp_path / "responses.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_row(code)))
        w.writeheader()
        w.writerow(_row(code))
    assert intake.import_rows(c, intake.read_csv(str(f)))[0]["status"] == "imported"
    md = intake.form_markdown()
    assert "**Your TR3D code**" in md and "   - Sanlam Cape Town Marathon" in md and intake.OTHER_RACE in md


def test_couch_to_5k_from_the_form(db):
    c, rid, code = db
    intake.import_rows(c, [_row(code, goal="Couch to 5K (I'm new to running)", recent="None in the last 6 months",
                                recent_time="", run_days="Monday, Tuesday, Thursday, Saturday")])
    d = dex.draft(c, rid)
    s = d["summary"]
    assert s["plan"] == "c25k" and s["weeks"] == 12 and len(s["days"]) == 3 and s["sessions"] == 36
    assert d["rows"][0]["description"].startswith("Run/walk — 6 × (1 min run / 2 min walk)")
    assert d["rows"][-1]["kind"] == "benchmark" and d["rows"][-1]["km"] == "5"
    assert dex.summary_text("Tom", d).startswith("Dex programme draft for Tom: Couch to 5K")


def test_race_plan_with_race_day_forecast(db, monkeypatch):
    c, rid, code = db
    intake.import_rows(c, [_row(code)])
    soon = (TODAY + timedelta(days=5)).isoformat()
    c.execute("UPDATE races SET date=?", (soon,))
    c.commit()

    def handler(req):
        hours = [f"{soon}T{h:02d}:00" for h in range(24)]
        return httpx.Response(200, json={"elevation": 10, "hourly": {"time": hours, "temperature_2m": [24.0] * 24,
                                                                      "dew_point_2m": [12.0] * 24,
                                                                      "relative_humidity_2m": [50] * 24}})
    real = httpx.Client
    monkeypatch.setattr(truepace.httpx, "get", lambda url, params, timeout: real(
        transport=httpx.MockTransport(handler)).get(url, params=params))
    truepace._cache.clear()
    p = raceplan.plan(c, rid)
    assert p["race"] == "Sanlam Cape Town Marathon" and p["predicted_finish_flat"].startswith("3:4")
    assert "Goal marathon pace" in p["personal_splits"] and "## The Course" in p["course_guide"]
    w = p["race_day_weather"]
    assert w["temp_c"] == 24 and w["slowdown"]["total_pct"] == 4.0 and p["goal_pace_race_day"] > p["goal_pace_flat"]
    assert "add 4%" in p["splits_note"] and "hills" in p
    c.execute("UPDATE races SET date=?", ((TODAY + timedelta(days=40)).isoformat(),))
    assert raceplan.plan(c, rid)["race_day_weather"].startswith("forecast available from")


def test_form_is_in_the_runners_own_record(db, tmp_path):
    from app import record
    c, rid, code = db
    intake.import_rows(c, [_row(code)])
    got = record.get_record(c, rid, "programme_form")
    a = got["items"][0]
    assert a["submitted_at"] == "2026-10-27T10:00:00" and a["answers"]["race"]["preset_id"] == "cape_town_marathon"
    assert record.get_record(c, rid, "programme_form", since="2026-10-27")["count"] == 1
    assert record.get_record(c, rid, "summary")["programme_forms"]["count"] == 1
    import sqlite3
    f = record.export_runner(c, rid, tmp_path / "tom.db")
    own = sqlite3.connect(f)
    row = own.execute("SELECT submitted_at, answers FROM dex_intake").fetchone()
    assert row[0] == "2026-10-27T10:00:00" and "Achilles" in row[1]
    assert own.execute("SELECT preset_id FROM races").fetchone()[0] == "cape_town_marathon"
