"""Garmin push: sender check, safe downloads, storage through the .fit pipeline, AI gate."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, garmin, hermes, main
from app import db as app_db
from app.admin import add_runner
from app.db import connect, init_db
from tests.fitgen import run_fit

CLIENT = "test-client-id"
URL = "https://apis.garmin.com/wellness-api/rest/activityFile?id=1&token=t"


def item(**kw):
    return {"userId": "garmin-user-1", "summaryId": "1001-file", "fileType": "FIT", "callbackURL": URL,
            "activityType": "RUNNING", "deviceName": "Forerunner 255", "activityId": 1001, "manual": False, **kw}


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "runners.db"
    monkeypatch.setattr(app_db, "settings", SimpleNamespace(db_path=str(path)))
    s = replace(config.settings, garmin_client_id=CLIENT, garmin_ai_allowed=False)
    monkeypatch.setattr(garmin, "settings", s)
    monkeypatch.setattr(main, "settings", s)
    c = connect(str(path))
    init_db(c)
    rid, _, _ = add_runner(c, "Andy", "111", 186, None)
    c.execute("UPDATE runners SET onboarded_at='2026-01-01', hermes_api_key='k'")
    c.commit()
    garmin.link(c, rid, "garmin-user-1")
    fit = run_fit(datetime.now(timezone.utc) - timedelta(hours=3), seconds=2400, hr=140)
    downloads, delivered = [], []
    monkeypatch.setattr(garmin, "download", lambda url: downloads.append(url) or fit)
    monkeypatch.setattr(hermes, "run_and_deliver", lambda r, p, **kw: delivered.append(p))
    yield SimpleNamespace(c=c, rid=rid, downloads=downloads, delivered=delivered, client=TestClient(main.app),
                          monkeypatch=monkeypatch, settings=s)
    c.close()


def post(env, path, body, client_id=CLIENT):
    return env.client.post(path, json=body, headers={"garmin-client-id": client_id} if client_id else {})


def test_rejects_pushes_without_our_client_id(env):
    assert post(env, "/garmin/activity-files", {"activityFiles": [item()]}, client_id=None).status_code == 401
    assert post(env, "/garmin/activity-files", {"activityFiles": [item()]}, client_id="other").status_code == 401
    assert env.downloads == []


def test_run_is_stored_with_device_and_no_ai_until_allowed(env):
    assert post(env, "/garmin/activity-files", {"activityFiles": [item()]}).status_code == 200
    row = env.c.execute("SELECT source, device, distance_km FROM runs").fetchone()
    assert tuple(row) == ("garmin", "Forerunner 255", 7.2)
    assert env.delivered == []                      # GARMIN_AI_ALLOWED is off
    # Same file again (e.g. also sent by hand): stored once.
    post(env, "/garmin/activity-files", {"activityFiles": [item(summaryId="1001-again")]})
    assert env.c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_debrief_carries_garmin_attribution_when_allowed(env):
    allowed = replace(env.settings, garmin_ai_allowed=True)
    env.monkeypatch.setattr(main, "settings", allowed)
    post(env, "/garmin/activity-files", {"activityFiles": [item()]})
    assert len(env.delivered) == 1
    p = env.delivered[0]
    assert '"Garmin Forerunner 255"' in p and "Insights derived in part from Garmin device-sourced data." in p


def test_unknown_users_and_other_sports_are_ignored(env):
    post(env, "/garmin/activity-files", {"activityFiles": [item(userId="someone-else"),
                                                           item(activityType="CYCLING", summaryId="x")]})
    assert env.downloads == []


def test_only_garmin_hosts_are_downloaded():
    assert garmin.safe_url(URL)
    for bad in ("http://apis.garmin.com/x", "https://evil.example/x", "https://apis.garmin.com.evil.io/x",
                "file:///etc/passwd", ""):
        assert not garmin.safe_url(bad)
    with pytest.raises(garmin.GarminError):
        garmin.download("https://evil.example/file.fit")


def test_deregistration_and_permissions_unlink(env):
    post(env, "/garmin/permissions", {"userPermissionsChange": [{"userId": "garmin-user-1",
                                                                 "permissions": ["ACTIVITY_EXPORT"]}]})
    assert env.c.execute("SELECT permissions FROM garmin_links").fetchone()[0] == '["ACTIVITY_EXPORT"]'
    post(env, "/garmin/deregistration", {"deregistrations": [{"userId": "garmin-user-1"}]})
    assert env.c.execute("SELECT COUNT(*) FROM garmin_links").fetchone()[0] == 0
    assert "Disconnected Garmin" in env.c.execute("SELECT message FROM coach_alerts").fetchone()[0]
