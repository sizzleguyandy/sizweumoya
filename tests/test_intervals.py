"""Intervals.icu: programme -> calendar events, activities and wellness back."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import intervals, programme
from app.admin import add_runner
from app.db import connect, init_db
from tests.fitgen import run_fit

TODAY = date.today()


@pytest.fixture
def db(tmp_path):
    c = connect(":memory:")
    init_db(c)
    rid, _, _ = add_runner(c, "Andy", "111", 186, None)
    c.execute("UPDATE runners SET easy_hr_cap=144")
    c.commit()
    d = lambda n: (TODAY + timedelta(days=n)).isoformat()
    f = tmp_path / "p.csv"
    f.write_text("date,type,description,km,kind,hr_low,hr_high,exercises,steps\n"
                 f"{d(1)},run,Easy run,5,easy,,,,\n"
                 f"{d(2)},run,Hill reps,7,workout,160,175,,\"- 15m Z2 HR|6x|- 90s Z4 HR|- 90s Z1 HR|- 10m Z2 HR\"\n"
                 f"{d(3)},strength,Upper A,,,,,\"Chest press 3x8; Lat pulldown 3x8\",\n")
    programme.load_csv(c, rid, str(f))
    intervals.connect(c, rid, "i12345", "an-intervals-api-key")
    yield c, rid
    c.close()


def mock(monkeypatch, handler):
    def client(link):
        return httpx.Client(base_url="https://intervals.icu/api/v1", auth=("API_KEY", link["api_key"]),
                            transport=httpx.MockTransport(handler))
    monkeypatch.setattr(intervals, "_client", client)


def test_dry_run_builds_watch_workouts(db):
    c, rid = db
    ev = intervals.push(c, rid, dry_run=True)["events"]
    easy, hills, gym = ev
    assert easy["category"] == "WORKOUT" and easy["type"] == "Run" and "- 5km Z2 HR" in easy["description"]
    assert "under 144 bpm" in easy["description"]
    assert "6x\n- 90s Z4 HR" in hills["description"] and "160-175 bpm" in hills["description"]
    assert gym["type"] == "WeightTraining" and "- Chest press 3x8" in gym["description"]
    assert easy["external_id"].startswith("sizwe-") and len({e["external_id"] for e in ev}) == 3


def test_push_uses_bulk_upsert_with_basic_auth(db, monkeypatch):
    c, rid = db
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}, {"id": 3}])
    mock(monkeypatch, handler)
    assert intervals.push(c, rid)["pushed"] == 3
    req = seen[0]
    assert req.url.path == "/api/v1/athlete/i12345/events/bulk" and req.url.params["upsert"] == "true"
    assert req.headers["authorization"].startswith("Basic ")
    assert len(json.loads(req.content)) == 3


def test_pull_activities_and_wellness(db, monkeypatch):
    c, rid = db
    fit = run_fit(datetime.now(timezone.utc) - timedelta(days=1), seconds=2400, hr=140)

    def handler(req):
        p = req.url.path
        if p.endswith("/activities"):
            return httpx.Response(200, json=[{"id": "a1", "source": "OAUTH_CLIENT"}, {"id": "a2", "source": "STRAVA"}])
        if p.endswith("/a1/fit-file"):
            return httpx.Response(200, content=fit)
        if p.endswith("/wellness"):
            return httpx.Response(200, json=[{"id": TODAY.isoformat(), "restingHR": 52, "hrv": 61.5,
                                              "sleepSecs": 26000, "weight": 96.5}])
        return httpx.Response(404)
    mock(monkeypatch, handler)
    res = intervals.pull_activities(c, rid, TODAY - timedelta(days=3))
    assert res["counts"]["stored"] == 1 and res["counts"]["skipped"] == 1   # Strava-sourced left out
    assert intervals.pull_activities(c, rid, TODAY - timedelta(days=3))["counts"]["duplicate"] == 1
    assert intervals.pull_wellness(c, rid, TODAY - timedelta(days=7)) == 1
    assert tuple(c.execute("SELECT resting_hr, hrv FROM wellness").fetchone()) == (52, 61.5)


def test_errors_are_reported(db, monkeypatch):
    c, rid = db
    mock(monkeypatch, lambda req: httpx.Response(401, text="bad key"))
    with pytest.raises(intervals.IntervalsError, match="401"):
        intervals.push(c, rid)
