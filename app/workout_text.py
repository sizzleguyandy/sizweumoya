"""Parse a strength workout the runner types into the chat.

Accepts one exercise per line (or separated by ';'), for example:

    Squat 3x8 60kg
    RDL 3 x 10 @ 50 kg
    Calf raises 3x15
    Plank 3x45s

Lines that don't match are kept in the raw text and reported as not parsed.
"""
import re
from dataclasses import dataclass

LB_TO_KG = 0.45359237

LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z '\-/()]*?)\s*[:\-]?\s*"
    r"(?P<sets>\d+)\s*[x×*]\s*(?P<reps>\d+)\s*(?P<secs>s|sec|secs|seconds)?\b"
    r"(?:\s*(?:@|at)?\s*(?P<w>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kgs|lb|lbs))?",
    re.IGNORECASE,
)


@dataclass
class TextSet:
    exercise: str
    set_no: int
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: int | None = None


def parse(text: str) -> tuple[list[TextSet], list[str]]:
    """Return (sets, unparsed_lines)."""
    sets: list[TextSet] = []
    unparsed: list[str] = []
    n = 0
    for raw in re.split(r"[\n;]+", text or ""):
        line = raw.strip(" -•\t")
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            unparsed.append(line)
            continue
        name = re.sub(r"\s+", " ", m["name"]).strip().lower()
        weight = None
        if m["w"]:
            weight = float(m["w"].replace(",", "."))
            if m["unit"].lower().startswith("lb"):
                weight = round(weight * LB_TO_KG, 2)
        for _ in range(min(int(m["sets"]), 20)):
            n += 1
            if m["secs"]:
                sets.append(TextSet(name, n, duration_s=int(m["reps"]), weight_kg=weight))
            else:
                sets.append(TextSet(name, n, reps=int(m["reps"]), weight_kg=weight))
    return sets, unparsed
