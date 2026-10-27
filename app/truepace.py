"""TRUE PACE: training paces adjusted for heat, humidity and altitude, from live weather.

Weather and elevation come from Open-Meteo (free, no key). The adjustments follow the ACSM-based
tables Coach Andy chose (baseline 10-15 °C, sea level to 450 m):

  heat     15-21 °C: 1-3%   21-27 °C: 3-5%   27-32 °C: 5-8%   above: +0.6% per °C
  humidity dew point over 18 °C: a further 2% rising to 5% at 24 °C
  altitude 900-1500 m: 1.5-3%   1500-2400 m: 3-6%   2400-3000 m: 6-10%

Within each band the slowdown rises in a straight line; the effects multiply when combined.
Faster runners (VDOT 60+) lose a little less in the heat (shorter exposure). Short repetitions
are not slowed for altitude (they don't depend on aerobic capacity). Heart rate still leads easy
running: in heat the pace slows and the cap stays.
"""
import logging
import time
from datetime import datetime, timedelta

import httpx

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
CACHE_S = 1800
MAX_SLOWDOWN = 20.0         # %: beyond this, pace targets stop meaning much
_cache: dict = {}

HEAT = [(15, 0.0), (15.01, 1.0), (21, 3.0), (27, 5.0), (32, 8.0)]
HUMID = [(18, 0.0), (18.01, 2.0), (24, 5.0)]
ALT = [(450, 0.0), (900, 1.5), (1500, 3.0), (2400, 6.0), (3000, 10.0)]


class WeatherError(RuntimeError):
    pass


def _interp(points, x: float, beyond_per_unit: float) -> float:
    if x <= points[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1] + (x - points[-1][0]) * beyond_per_unit


def slowdown(temp_c: float, dew_point_c: float, elevation_m: float, vdot: float | None = None) -> dict:
    """Percent slower for each factor, and combined."""
    heat = _interp(HEAT, temp_c, 0.6)
    humid = _interp(HUMID, dew_point_c, 0.0)
    if vdot and vdot >= 60:
        heat, humid = heat * 0.85, humid * 0.85
    alt = _interp(ALT, elevation_m, 0.0067)
    weather = (1 + heat / 100) * (1 + humid / 100)
    total = min((weather * (1 + alt / 100) - 1) * 100, MAX_SLOWDOWN)
    return {"heat_pct": round(heat, 1), "humidity_pct": round(humid, 1), "altitude_pct": round(alt, 1),
            "weather_pct": round(min((weather - 1) * 100, MAX_SLOWDOWN), 1), "total_pct": round(total, 1)}


def warnings(temp_c: float, dew_point_c: float, total_pct: float) -> list[str]:
    out = []
    if temp_c >= 32 or dew_point_c >= 24 or total_pct >= 12:
        out.append("high heat stress: run early or late, shorten it, or move hard sessions to a cooler day")
    elif temp_c >= 27 or dew_point_c >= 21:
        out.append("hot: drink before and during, and run hard sessions by effort, not pace")
    return out


# ---- Open-Meteo -----------------------------------------------------------------------------

def _get(url: str, params: dict) -> dict:
    key = (url, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    try:
        r = httpx.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise WeatherError(f"weather service didn't answer ({e.__class__.__name__})") from None
    _cache[key] = (time.time(), data)
    return data


def find_place(name: str) -> dict:
    """A town or city -> name, country, latitude, longitude, elevation, timezone."""
    data = _get(GEOCODE_URL, {"name": name.strip(), "count": 1, "language": "en", "format": "json"})
    res = (data.get("results") or [])
    if not res:
        raise WeatherError(f"couldn't find a place called {name!r}")
    p = res[0]
    return {"name": p["name"], "country": p.get("country"), "latitude": p["latitude"], "longitude": p["longitude"],
            "elevation_m": p.get("elevation"), "timezone": p.get("timezone")}


def forecast(lat: float, lon: float, when: datetime) -> dict:
    """Temperature, dew point and humidity for the hour nearest `when` (local time there),
    up to 16 days ahead, plus the elevation of that spot."""
    days = max(1, min(16, (when.date() - datetime.now().date()).days + 2))
    data = _get(FORECAST_URL, {"latitude": round(lat, 3), "longitude": round(lon, 3), "timezone": "auto",
                               "hourly": "temperature_2m,dew_point_2m,relative_humidity_2m",
                               "forecast_days": days})
    h = data.get("hourly") or {}
    times = h.get("time") or []
    if not times:
        raise WeatherError("no forecast for that place")
    target = when.strftime("%Y-%m-%dT%H:00")
    if target < times[0] or target > times[-1]:
        raise WeatherError("that's further ahead than the forecast goes (16 days)")
    i = min(range(len(times)), key=lambda k: abs(datetime.fromisoformat(times[k]) - when.replace(minute=0)))
    return {"time": times[i], "temp_c": h["temperature_2m"][i], "dew_point_c": h["dew_point_2m"][i],
            "humidity_pct": h["relative_humidity_2m"][i], "elevation_m": data.get("elevation")}


# ---- the TRUE PACE answer ---------------------------------------------------------------------

def _fmt(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}/km"


def _parse(p: str) -> float:
    m, s = p.split("/")[0].strip().split(":")
    return int(m) * 60 + int(s)


def true_pace(place: dict, when: datetime, performance: dict | None, easy_hr_cap: int | None) -> dict:
    """Weather at the run, the slowdown, and the runner's paces adjusted for it."""
    w = forecast(place["latitude"], place["longitude"], when)
    elev = place.get("elevation_m") if place.get("elevation_m") is not None else (w["elevation_m"] or 0)
    vdot = performance.get("vdot") if performance else None
    s = slowdown(w["temp_c"], w["dew_point_c"], elev, vdot)
    out = {"place": place["name"], "forecast_for": w["time"], "temp_c": w["temp_c"],
           "dew_point_c": w["dew_point_c"], "humidity_pct": w["humidity_pct"], "elevation_m": round(elev),
           "slowdown": s, "warnings": warnings(w["temp_c"], w["dew_point_c"], s["total_pct"]),
           "source": "Open-Meteo forecast"}
    if s["altitude_pct"]:
        out["acclimatisation"] = "altitude takes 2-3 weeks to adapt to; heat takes 7-14 days"
    elif s["weather_pct"] >= 3:
        out["acclimatisation"] = "heat takes 7-14 days to adapt to"
    if easy_hr_cap:
        out["easy_hr_cap"] = easy_hr_cap
        out["heart_rate"] = ("keep easy runs under the same heart rate cap; the pace is what slows"
                             if s["total_pct"] else "normal conditions")
    if performance and performance.get("available"):
        f_all = 1 + s["total_pct"] / 100
        f_weather = 1 + s["weather_pct"] / 100              # short reps: no altitude slowdown
        adj = {}
        for name, band in performance["training_paces"].items():
            f = f_weather if name == "repetition" else f_all
            adj[name] = {"from": _fmt(_parse(band["from"]) * f), "to": _fmt(_parse(band["to"]) * f),
                         "normal": f"{band['from']}-{band['to']}"}
        out["true_paces_per_km"] = adj
    else:
        out["true_paces_per_km"] = None
        out["no_paces_because"] = "no recent race or time trial on file; the slowdown % still applies"
    return out


def default_time(now: datetime | None = None) -> datetime:
    """The next full hour, or 06:00 tomorrow if it's already evening."""
    now = now or datetime.now()
    if now.hour >= 20:
        return (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
