"""Parse a strength workout the runner types or pastes into the chat.

Accepted, in any mix:

    Squat 3x8 60kg                      one exercise per line (or separated by ';')
    RDL 3 x 10 @ 50 kg
    Shoulder press 3x10 + 2x7 30kg      rep counts that change between sets
    Plank 3x45s

and the text Hevy produces when a workout is shared:

    Sumo Deadlift
    Set 1: 60 kg x 5
    Set 2: 90 kg x 5

Title, date, "@hevyapp" and link lines from a shared workout are ignored. Anything else that
doesn't match is reported back as not understood, never guessed.
"""
import re
from dataclasses import dataclass

LB_TO_KG = 0.45359237

NUM = r"\d+(?:[.,]\d+)?"
WEIGHT = rf"(?P<w>{NUM})\s*(?P<unit>kg|kgs|lb|lbs)"
GROUP_RE = re.compile(rf"(?P<sets>\d+)\s*[x×*]\s*(?P<reps>\d+)\s*(?P<secs>s|sec|secs|seconds)?\b"
                      rf"(?:\s*(?:@|at)?\s*{WEIGHT})?", re.IGNORECASE)
NAME_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z '\-/()&]*?)\s*[:\-]?\s*(?=\d)")
# Hevy share lines: "Set 2: 90 kg x 5", "Set 1: 12 reps", "Set 3: 45s", "Set 1: 20 lbs x 10"
SET_RE = re.compile(
    rf"^set\s*\d+\s*:\s*(?:{WEIGHT}\s*[x×*]\s*(?P<reps>\d+)"
    rf"|(?P<reps_only>\d+)\s*reps?|(?P<secs>\d+)\s*(?:s|sec|secs|seconds)\b"
    rf"|(?P<w2>{NUM})\s*(?P<unit2>kg|kgs|lb|lbs)\s*[x×*]\s*(?P<secs2>\d+)\s*(?:s|sec|secs|seconds)\b)",
    re.IGNORECASE)
NOISE_RE = re.compile(
    r"^(@hevyapp|https?://\S+|(mon|tues|wednes|thurs|fri|satur|sun)day\b.*\d{4}.*"
    r"|(morning|afternoon|evening|night|lunch(time)?)\s+workout\b.*)$", re.IGNORECASE)


@dataclass
class TextSet:
    exercise: str
    set_no: int
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: int | None = None


def _kg(value: str | None, unit: str | None) -> float | None:
    if not value:
        return None
    w = float(value.replace(",", "."))
    return round(w * LB_TO_KG, 2) if unit and unit.lower().startswith("lb") else w


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" :-").lower()


def parse(text: str) -> tuple[list[TextSet], list[str]]:
    """Return (sets, unparsed_lines)."""
    sets: list[TextSet] = []
    unparsed: list[str] = []
    counts: dict[str, int] = {}
    pending_name: str | None = None      # an exercise header waiting for its "Set n:" lines
    pending_used = False

    def add(name: str, **kw) -> None:
        counts[name] = counts.get(name, 0) + 1
        sets.append(TextSet(name, counts[name], **kw))

    def flush_header() -> None:
        nonlocal pending_name, pending_used
        if pending_name and not pending_used:
            unparsed.append(pending_name)
        pending_name, pending_used = None, False

    for raw in re.split(r"[\n;]+", text or ""):
        line = raw.strip(" -•\t")
        if not line:
            continue
        m = SET_RE.match(line)
        if m and pending_name:
            name = _clean_name(pending_name)
            if m["reps"]:
                add(name, reps=int(m["reps"]), weight_kg=_kg(m["w"], m["unit"]))
            elif m["reps_only"]:
                add(name, reps=int(m["reps_only"]))
            elif m["secs"]:
                add(name, duration_s=int(m["secs"]))
            else:
                add(name, duration_s=int(m["secs2"]), weight_kg=_kg(m["w2"], m["unit2"]))
            pending_used = True
            continue
        if NOISE_RE.match(line):
            continue
        n = NAME_RE.match(line)
        groups = list(GROUP_RE.finditer(line, n.end())) if n else []
        if n and groups:
            flush_header()
            name = _clean_name(n["name"])
            # A weight written once at the end ("3x10 + 2x7 30kg") applies to every group without one.
            shared = next((g for g in reversed(groups) if g["w"]), None)
            for g in groups:
                w = _kg(g["w"], g["unit"]) if g["w"] else (_kg(shared["w"], shared["unit"]) if shared else None)
                for _ in range(min(int(g["sets"]), 20)):
                    if g["secs"]:
                        add(name, duration_s=int(g["reps"]), weight_kg=w)
                    else:
                        add(name, reps=int(g["reps"]), weight_kg=w)
            continue
        # Not a set line: possibly the header of a shared Hevy exercise.
        flush_header()
        if re.match(r"^[A-Za-z][A-Za-z '\-/()&]*$", line):
            pending_name = line
        else:
            unparsed.append(line)
    flush_header()
    return sets, unparsed
