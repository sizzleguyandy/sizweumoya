"""Parse the workout text Hevy writes into a Strava activity description.

Expected shape (confirm against a real export and adjust SET_RE if needed):

    Squat (Barbell)
    Set 1: 60 kg x 8
    Set 2: 65 kg x 6 [Warm-up]

    Plank
    Set 1: 0:45

Unparseable text is kept as raw_description so it can be re-parsed later.
"""
import re
from dataclasses import dataclass

LB_TO_KG = 0.45359237

SET_RE = re.compile(
    r"^set\s*(?P<no>\d+)\s*:\s*(?P<body>.+?)\s*(?:\[[^\]]*\])?\s*$", re.IGNORECASE
)
WEIGHT_REPS_RE = re.compile(
    r"^(?P<sign>[+-])?\s*(?P<w>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kgs|lb|lbs)\s*[x×]\s*(?P<reps>\d+)",
    re.IGNORECASE,
)
REPS_ONLY_RE = re.compile(r"^(?P<reps>\d+)\s*reps?\b", re.IGNORECASE)
DURATION_RE = re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})\b")
SECONDS_RE = re.compile(r"^(?P<s>\d+)\s*s(?:ec(?:onds?)?)?\b", re.IGNORECASE)
DISTANCE_RE = re.compile(r"^(?P<d>\d+(?:[.,]\d+)?)\s*(?P<unit>km|m|mi)\b", re.IGNORECASE)

SKIP_LINES = re.compile(r"^(logged with hevy|https?://|#)", re.IGNORECASE)


@dataclass
class ParsedSet:
    exercise: str
    set_no: int
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: int | None = None
    distance_m: float | None = None


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_set_body(body: str) -> dict | None:
    body = body.strip()
    if m := WEIGHT_REPS_RE.match(body):
        w = _num(m["w"])
        if m["unit"].lower().startswith("lb"):
            w *= LB_TO_KG
        if m["sign"] == "-":
            w = -w  # assisted exercise
        return {"weight_kg": round(w, 2), "reps": int(m["reps"])}
    if m := REPS_ONLY_RE.match(body):
        return {"reps": int(m["reps"])}
    if m := DURATION_RE.match(body):
        return {"duration_s": int(m["h"] or 0) * 3600 + int(m["m"]) * 60 + int(m["s"])}
    if m := SECONDS_RE.match(body):
        return {"duration_s": int(m["s"])}
    if m := DISTANCE_RE.match(body):
        d, unit = _num(m["d"]), m["unit"].lower()
        return {"distance_m": d * {"km": 1000, "m": 1, "mi": 1609.344}[unit]}
    return None


def parse_description(text: str | None) -> tuple[list[ParsedSet], bool]:
    """Return (sets, ok). ok is False when nothing usable was found or a set line failed."""
    if not text:
        return [], False
    sets: list[ParsedSet] = []
    exercise: str | None = None
    failures = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or SKIP_LINES.match(line):
            continue
        if m := SET_RE.match(line):
            parsed = parse_set_body(m["body"])
            if exercise is None or parsed is None:
                failures += 1
                continue
            sets.append(ParsedSet(exercise=exercise, set_no=int(m["no"]), **parsed))
        else:
            exercise = line
    return sets, bool(sets) and failures == 0


_NAME_CLEAN = re.compile(r"[^a-z0-9]+")


def normalise_exercise(name: str) -> str:
    """'Squat (Barbell)' -> 'squat_barbell'. Extend with an alias table if needed."""
    return _NAME_CLEAN.sub("_", name.lower()).strip("_")
