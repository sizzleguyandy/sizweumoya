"""TRUE PACE: heat, dew point and altitude slowdown, and paces from a (mocked) forecast."""
from datetime import datetime

import httpx
import pytest

from app import truepace


def test_slowdown_follows_the_science_table():
    assert truepace.slowdown(12, 8, 0)["total_pct"] == 0
    assert truepace.slowdown(18, 10, 0)["heat_pct"] == 2.0                # 15-21 °C: 1-3%
    assert truepace.slowdown(24, 10, 0)["heat_pct"] == 4.0                # 21-27 °C: 3-5%
    assert truepace.slowdown(30, 10, 0)["heat_pct"] == pytest.approx(6.8) # 27-32 °C: 5-8%
    assert truepace.slowdown(24, 21, 0)["humidity_pct"] == 3.5            # dew point over 18: 2-5%
    assert truepace.slowdown(12, 8, 1200)["altitude_pct"] == 2.2          # 900-1500 m: 1.5-3%
    assert truepace.slowdown(12, 8, 1950)["altitude_pct"] == 4.5          # 1500-2400 m: 3-6%
    hot_high = truepace.slowdown(30, 20, 1600)                             # "Denver in July"
    assert 10 <= hot_high["total_pct"] <= 15
    assert truepace.slowdown(30, 10, 0, vdot=62)["heat_pct"] < 6.8         # faster runners lose a little less
    assert truepace.slowdown(45, 30, 3500)["total_pct"] == truepace.MAX_SLOWDOWN


def test_true_pace_from_forecast(monkeypatch):
    when = datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
    hours = [when.replace(hour=h).strftime("%Y-%m-%dT%H:00") for h in range(24)]

    def handler(req):
        assert req.url.host == "api.open-meteo.com" and "dew_point_2m" in str(req.url)
        return httpx.Response(200, json={"elevation": 25, "hourly": {
            "time": hours, "temperature_2m": [26.0] * 24, "dew_point_2m": [20.0] * 24,
            "relative_humidity_2m": [70] * 24}})
    real = httpx.Client
    monkeypatch.setattr(truepace.httpx, "get", lambda url, params, timeout: real(
        transport=httpx.MockTransport(handler)).get(url, params=params))
    truepace._cache.clear()
    perf = {"available": True, "vdot": 42, "training_paces": {
        "easy": {"from": "5:30/km", "to": "6:30/km"}, "threshold": {"from": "4:50/km", "to": "5:05/km"}}}
    out = truepace.true_pace({"name": "Cape Town", "latitude": -33.9, "longitude": 18.4, "elevation_m": 25},
                             when, perf, 144)
    assert out["forecast_for"].endswith("T07:00") and out["temp_c"] == 26
    s = out["slowdown"]
    assert s["heat_pct"] == pytest.approx(4.7) and s["humidity_pct"] == 3.0 and s["altitude_pct"] == 0
    assert out["true_paces_per_km"]["threshold"]["from"] == "5:13/km"      # 290 s x 1.075
    assert out["easy_hr_cap"] == 144 and "same heart rate cap" in out["heart_rate"]
    assert out["warnings"] == [] and out["acclimatisation"].startswith("heat")
    no_perf = truepace.true_pace({"name": "x", "latitude": 1, "longitude": 1, "elevation_m": 0}, when, None, None)
    assert no_perf["true_paces_per_km"] is None


def test_default_time():
    assert truepace.default_time(datetime(2026, 10, 27, 21, 15)) == datetime(2026, 10, 28, 6, 0)
    assert truepace.default_time(datetime(2026, 10, 27, 9, 15)) == datetime(2026, 10, 27, 10, 0)
