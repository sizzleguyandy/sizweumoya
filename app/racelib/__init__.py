"""The race library, from TR3D Run Coach: SA and UK races with distance, climb, hilliness,
usual date, start location, a course guide and (for most) a personal pacing calculator.

The usual dates are a guide only: race dates move (Two Oceans follows Easter), so the date the
runner or the programme form gives always wins. Guides are shown without their "Tips for TR3D
Runners" sections until Coach Andy has reviewed those (several tell marathoners to hold
threshold pace, which is too fast for a marathon).
"""
import re
from datetime import date

from .knowledge import RACE_DISPLAY_NAMES, get_race_context, load_race_knowledge
from .presets_sa import RACE_COORDS_SA, RACE_PRESETS_SA
from .presets_uk import RACE_COORDS_UK, RACE_PRESETS_UK

PRESETS = {**RACE_PRESETS_SA, **RACE_PRESETS_UK}
COORDS = {**RACE_COORDS_SA, **RACE_COORDS_UK}
UNREVIEWED_SECTION = "Tips for TR3D Runners"


def _usual_date(p: dict, today: date) -> str:
    d = date(today.year, p["typical_month"], p["typical_day"])
    if d <= today:
        d = date(today.year + 1, p["typical_month"], p["typical_day"])
    return d.isoformat()


def info(race_id: str, today: date | None = None) -> dict | None:
    p = PRESETS.get(race_id)
    if p is None:
        return None
    lat, lon = COORDS.get(race_id, (None, None))
    return {"id": race_id, "name": RACE_DISPLAY_NAMES.get(race_id, p["display_name"]),
            "country": p["country"].upper(), "distance_km": p["exact_distance_km"],
            "climb_m": p["elevation_gain_m"], "hilliness": p["hilliness"],
            "usual_date": _usual_date(p, today or date.today()), "date_note": "usual date only: confirm this year's",
            "start": {"latitude": lat, "longitude": lon} if lat is not None else None,
            "about": p["description"]}


def all_races(today: date | None = None) -> list[dict]:
    return sorted((info(k, today) for k in PRESETS), key=lambda r: (r["country"], r["name"]))


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in {"the", "marathon", "race", "run"}}


def find(text: str, today: date | None = None) -> list[dict]:
    """Library races matching what someone typed ("cape town", "Oceans", "comrades 2027")."""
    t = (text or "").strip().lower()
    if not t:
        return []
    want = _words(t)
    out = []
    for k, p in PRESETS.items():
        names = {k.replace("_", " "), p["display_name"].lower(), RACE_DISPLAY_NAMES.get(k, "").lower()}
        if any(t in n or n in t for n in names if n) or (want and want <= set().union(*map(_words, names))):
            out.append(info(k, today))
    return out


def guide(race_id: str) -> str | None:
    """The course guide, without the sections Coach Andy hasn't reviewed yet."""
    text = load_race_knowledge(race_id)
    if not text:
        return None
    return re.sub(rf"\n## {re.escape(UNREVIEWED_SECTION)}.*?(?=\n## |\Z)", "", text, flags=re.S).strip()


def pacing(race_id: str, vdot: float, race_date: str | None = None) -> str | None:
    """Personal splits or checkpoints for this race at this VDOT (flat-weather numbers)."""
    return get_race_context(race_id, vdot, race_date).get("checkpoint_summary") or None
