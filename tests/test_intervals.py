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


def test_long_run_with_faster_parts_is_not_sent_as_all_easy(db):
    c, rid = db
    pid = c.execute("SELECT plan_id FROM planned_workouts LIMIT 1").fetchone()[0]
    c.execute("INSERT INTO planned_workouts (plan_id, runner_id, date, description, target) VALUES (?,?,?,?,?)",
              (pid, rid, (TODAY + timedelta(days=4)).isoformat(), "Hilly. Last 20min @ HM effort",
               json.dumps({"kind": "long", "km": 16, "hr_range": [152, 163]})))
    c.commit()
    ev = [e for e in intervals.push(c, rid, dry_run=True)["events"] if "Hilly" in e["name"]][0]
    assert "- 16km Z2 HR" not in ev["description"]
    assert "Easy running: under 144 bpm" in ev["description"] and "Faster parts: 152-163 bpm" in ev["description"]


def test_short_titles():
    assert intervals.short_name("LONG HILL REPS — 15min wu + 4 x 3min climbs") == "LONG HILL REPS"
    assert intervals.short_name("Hilly. Last 20min @ HM effort") == "Hilly"
    assert intervals.short_name("Easy run") == "Easy run"
    assert len(intervals.short_name("x" * 80)) == 40


def test_anything_from_strava_is_recognised():
    assert intervals.from_strava({"source": "STRAVA"})
    # A Zepp run Intervals.icu also links to its Strava copy is still the person's own watch file.
    assert not intervals.from_strava({"source": "ZEPP", "strava_id": 20723779993,
                                      "device_name": "ZEPP Amazfit Balance 2"})
    assert intervals.from_strava({"_note": "STRAVA activities are not available via the API"})
    assert not intervals.from_strava({"source": "OAUTH_CLIENT", "device_name": "Amazfit Balance 2"})


def test_reconcile_updates_and_removes_only_our_stale_entries(db, monkeypatch):
    c, rid = db
    ids = [r[0] for r in c.execute("SELECT id FROM planned_workouts ORDER BY date")]
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        if req.method == "POST":
            return httpx.Response(200, json=[])
        if req.method == "GET":
            return httpx.Response(200, json=[
                {"id": 11, "external_id": f"sizwe-{ids[0]}"},        # still in the programme: keep
                {"id": 12, "external_id": "sizwe-999999"},            # session no longer exists: remove
                {"id": 13, "external_id": None},                      # added by the person: never touch
                {"id": 14, "external_id": "someone-else-7"}])
        return httpx.Response(200, json={})
    mock(monkeypatch, handler)
    res = intervals.reconcile(c, rid)
    assert res == {"sent": 3, "removed": 1}
    assert ("DELETE", "/api/v1/athlete/i12345/events/12") in calls
    assert not any(m == "DELETE" and p.endswith(("/11", "/13", "/14")) for m, p in calls)
    # a session Coach Andy made a rest day: not sent, and its entry goes
    from app import plans
    plans.edit_session(c, rid, c.execute("SELECT date FROM planned_workouts WHERE id=?", (ids[0],)).fetchone()[0],
                       rest=True)
    calls.clear()
    assert intervals.reconcile(c, rid) == {"sent": 2, "removed": 2}
    assert ("DELETE", "/api/v1/athlete/i12345/events/11") in calls
    c.execute("UPDATE planned_workouts SET status='planned' WHERE id=?", (ids[0],))
    # a replaced programme: its entries go
    c.execute("UPDATE plans SET active=0")
    c.commit()
    calls.clear()
    assert intervals.reconcile(c, rid)["removed"] == 2


# ---- athletes linked through Coach Andy's account ---------------------------------------

def _coached(c):
    rid2, _, _ = add_runner(c, "Tom", "222", None, None)
    return rid2


def test_link_through_coach_needs_no_key_and_uses_coach_key(db, monkeypatch):
    c, coach = db
    tom = _coached(c)
    with pytest.raises(intervals.IntervalsError):
        intervals.normalise_athlete_id("my password")
    assert intervals.request_link(c, tom, "I777 ")["athlete_id"] == "i777"
    with pytest.raises(intervals.IntervalsError):      # not active until approved and accepted
        intervals.link_for(c, tom)
    with pytest.raises(intervals.IntervalsError):      # no coach account marked yet
        intervals.link_for(c, tom, any_status=True)
    intervals.set_coach(c, coach)
    link = intervals.link_for(c, tom, any_status=True)
    assert link["api_key"] == "an-intervals-api-key" and link["via_coach"] == 1
    stored = c.execute("SELECT api_key FROM intervals_links WHERE runner_id=?", (tom,)).fetchone()[0]
    assert stored == ""                                  # nobody else's key is ever kept


def test_athlete_id_cannot_be_claimed_twice(db):
    c, coach = db
    tom = _coached(c)
    with pytest.raises(intervals.IntervalsError, match="someone else"):
        intervals.request_link(c, tom, "i12345")         # Coach Andy's own athlete ID
    with pytest.raises(intervals.IntervalsError, match="own key"):
        intervals.request_link(c, coach, "i999")


def test_zones_from_intervals_threshold():
    a = {"sportSettings": [{"types": ["Ride"], "lthr": 170},
                           {"types": ["Run", "VirtualRun"], "lthr": 165, "max_hr": 186}]}
    assert intervals.zones_from_athlete(a) == {"lthr": 165, "max_hr": 186, "zones": [132, 144, 152, 162]}
    assert intervals.zones_from_athlete({"sportSettings": [{"types": ["Run"]}]}) is None


def test_link_goes_live_once_coach_request_is_accepted(db, monkeypatch):
    from app import onboarding
    c, coach = db
    intervals.set_coach(c, coach)
    tom = _coached(c)
    intervals.request_link(c, tom, "i777")
    intervals.approve_link(c, tom)
    accepted = {"yes": False}

    def handler(req):
        p = req.url.path
        assert req.headers["authorization"].startswith("Basic ")
        if p == "/api/v1/athlete/i777":
            if not accepted["yes"]:
                return httpx.Response(403, json={"error": "denied"})
            return httpx.Response(200, json={"sportSettings": [{"types": ["Run"], "lthr": 170, "max_hr": 190}]})
        if p.endswith("/activities") or p.endswith("/wellness") or p.endswith("/events"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)
    mock(monkeypatch, handler)
    assert onboarding.complete_intervals_link(c, tom) is None
    assert c.execute("SELECT status FROM intervals_links WHERE runner_id=?", (tom,)).fetchone()[0] == "approved"
    accepted["yes"] = True
    out = onboarding.complete_intervals_link(c, tom)
    assert out["activities"]["stored"] == 0 and out["zones_request"]
    assert c.execute("SELECT status FROM intervals_links WHERE runner_id=?", (tom,)).fetchone()[0] == "active"
    z = c.execute("SELECT kind, status, payload FROM approvals WHERE runner_id=?", (tom,)).fetchone()
    assert z["kind"] == "zones" and z["status"] == "pending" and json.loads(z["payload"])["max_hr"] == 190
