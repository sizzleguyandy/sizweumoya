"""Structured workouts: one step model, written out two ways.

- Intervals.icu workout text (the programme's `steps`): Intervals.icu turns it into a structured
  workout and sends it to Garmin Connect (and Zepp), so the watch guides the run step by step.
- A Garmin FIT workout file (File Id type 5, Workout, Workout Steps), per the FIT SDK's
  Workout File description: heart-rate targets as absolute bpm (value + 100), repeats as a
  repeat_until_steps_cmplt step after the block. For copying straight onto a watch.

Heart-rate targets are zones 1-5 of the runner's own zones (tops of Z1-Z4 plus max HR), so the
watch, Sarah and the reports agree. Short repetitions and sprints have no heart-rate target
(heart rate can't catch up in 30 seconds): they run by pace or effort. Recoveries have none
either, so the watch doesn't nag while heart rate comes down.
"""
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Step intensity (FIT `intensity`)
ACTIVE, REST, WARMUP, COOLDOWN, RECOVERY, INTERVAL = 0, 1, 2, 3, 4, 5
PACE_ZONE = {"E": 2, "M": 3, "T": 4, "I": 5, "R": None}
FULL_RECOVERY_S = 90


@dataclass
class Step:
    intensity: int
    kind: str                      # "distance" (metres), "time" (seconds) or "open"
    value: float = 0
    zone: int | None = None        # 1-5, or None for no heart-rate target
    name: str = ""


@dataclass
class Repeat:
    times: int
    steps: list = field(default_factory=list)


# ---- from Dex's session text --------------------------------------------------------------

_NUM = r"(\d+(?:\.\d+)?)"


def _dur(n: str, unit: str) -> tuple[str, float]:
    v = float(n)
    unit = unit.lower()
    if unit in ("km",):
        return "distance", v * 1000
    if unit in ("m",):
        return "distance", v
    if unit.startswith("min"):
        return "time", v * 60
    return "time", v                                   # sec / s


def _recovery(text: str, rep: Step) -> Step:
    """The recovery after each rep, from text like "1 min jog", "200m jog", "2 min rest"."""
    m = re.search(rf"{_NUM}\s*(min|sec|s|m|km)\b\.?\s*(?:jog|rest|walk|easy|recovery)", text)
    if m:
        kind, v = _dur(m.group(1), m.group(2))
        return Step(RECOVERY, kind, v, None, "Recover")
    if "equal to rep time" in text and rep.kind == "time":
        return Step(RECOVERY, "time", rep.value, None, "Recover")
    return Step(RECOVERY, "time", FULL_RECOVERY_S, None, "Recover")


def _main(seg: str) -> list | None:
    seg = seg.replace("×", "x")
    pace = re.search(r"@\s*([EMTIR])[ -]pace", seg)
    zone = PACE_ZONE[pace.group(1)] if pace else None
    if re.search(r"\ball-out\b|\bsprint", seg):
        zone = None
    m = re.match(rf"\s*{_NUM}\s*(min|sec)\s+continuous", seg)
    if m:
        kind, v = _dur(m.group(1), m.group(2))
        return [Step(ACTIVE, kind, v, zone, "Steady")]
    m = re.match(rf"\s*(?:(\d+)\s+sets of\s+)?(\d+)\s*x\s*~?{_NUM}(?:\s*[–-]\s*{_NUM})?\s*(min|sec|s|m|km)\b(.*)", seg)
    if not m:
        return None
    sets, reps, unit, rest = m.group(1), int(m.group(2)), m.group(5), m.group(6)
    n = m.group(4) or m.group(3)                       # "10-15 sec": the top of the range
    if "+" in rest.split("@")[0]:
        return None                                    # mixed ladders: leave as a note
    kind, v = _dur(n, unit)
    rep = Step(INTERVAL, kind, v, zone, "Hard")
    block = Repeat(reps, [rep, _recovery(rest, rep)])
    if not sets:
        return [block]
    between = re.search(rf"{_NUM}\s*(m|km|min)\s+jog between sets", rest)
    out = []
    for i in range(int(sets)):
        out.append(Repeat(reps, list(block.steps)))
        if i < int(sets) - 1 and between:
            k, bv = _dur(between.group(1), between.group(2))
            out.append(Step(RECOVERY, k, bv, None, "Between sets"))
    return out


def from_dex(notes: str) -> list | None:
    """Dex/TR3D session text ("WU 2.0 km easy | 5 x 2 min hard @ I pace — 1 min jog | CD 1.0 km
    easy") -> steps, or None when it can't be read safely (then it stays a calendar note)."""
    steps = []
    main_found = False
    for seg in [s.strip() for s in (notes or "").split("|")]:
        wu = re.match(rf"(WU|CD)\s+{_NUM}\s*km", seg)
        if wu:
            steps.append(Step(WARMUP if wu.group(1) == "WU" else COOLDOWN, "distance", float(wu.group(2)) * 1000, 2,
                              "Warm up" if wu.group(1) == "WU" else "Cool down"))
            continue
        main = _main(seg.split(" — ")[0] + (" — " + seg.split(" — ", 1)[1] if " — " in seg else ""))
        if main is None:
            return None
        steps += main
        main_found = True
    return steps if main_found else None


# ---- Intervals.icu workout text -----------------------------------------------------------

def _dur_text(s: Step) -> str:
    if s.kind == "distance":
        return f"{s.value / 1000:g}km" if s.value >= 1000 else f"{s.value:g}mtr"   # Intervals.icu: m = minutes
    if s.kind == "time":
        return f"{s.value / 60:g}m" if s.value % 60 == 0 else f"{s.value:g}s"
    return "1m"                                                                  # open: 1 minute


def _line(s: Step) -> str:
    target = f" Z{s.zone} HR" if s.zone else ""
    return f"- {s.name + ' ' if s.name else ''}{_dur_text(s)}{target}".rstrip()


def to_intervals(steps: list) -> str:
    """Lines Intervals.icu reads as a workout (blank line around repeat blocks)."""
    out = []
    for s in steps:
        if isinstance(s, Repeat):
            out += ["", f"{s.times}x"] + [_line(x) for x in s.steps] + [""]
        else:
            out.append(_line(s))
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- Garmin FIT workout file ---------------------------------------------------------------

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
_ENUM, _UINT16, _UINT32, _STRING = 0x00, 0x84, 0x86, 0x07
_FMT = {_ENUM: "B", _UINT16: "H", _UINT32: "I"}
_SIZE = {_ENUM: 1, _UINT16: 2, _UINT32: 4}
DUR_TIME, DUR_DISTANCE, DUR_OPEN, DUR_REPEAT = 0, 1, 5, 6
TGT_HR, TGT_OPEN = 1, 2


def _crc(data: bytes) -> int:
    table = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    crc = 0
    for byte in data:
        for nib in (byte & 0xF, byte >> 4):
            tmp = table[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ table[nib]
    return crc


class _Fit:
    def __init__(self):
        self.body = bytearray()
        self.local = {}

    def msg(self, global_num: int, fields: list[tuple[int, int, object, int]]):
        """fields: (field number, base type, value, size for strings)."""
        sig = (global_num, tuple((f, b, n) for f, b, _, n in fields))
        if sig not in self.local:
            lid = len(self.local) % 16
            self.local[sig] = lid
            self.body += struct.pack("<BBBHB", 0x40 | lid, 0, 0, global_num, len(fields))
            for f, b, _, n in fields:
                self.body += struct.pack("<BBB", f, n if b == _STRING else _SIZE[b], b)
        self.body += struct.pack("<B", self.local[sig])
        for _, b, v, n in fields:
            if b == _STRING:
                raw = str(v).encode("utf-8")[: n - 1]
                self.body += raw + b"\0" * (n - len(raw))
            else:
                self.body += struct.pack("<" + _FMT[b], int(v))

    def bytes(self) -> bytes:
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self.body), b".FIT")
        header += struct.pack("<H", _crc(header))
        data = header + bytes(self.body)
        return data + struct.pack("<H", _crc(data))


def _zone_bpm(zone: int, zones: list[int], max_hr: int | None) -> tuple[int, int]:
    lows = [90] + [z + 1 for z in zones]
    highs = zones + [max_hr or zones[-1] + 15]
    return lows[zone - 1], highs[zone - 1]


def to_fit(steps: list, name: str, zones: list[int], max_hr: int | None, serial: int = 1) -> bytes:
    """A Garmin FIT workout file: File Id (type 5), Workout, then the steps in order."""
    flat: list[tuple] = []                      # ("step", Step) or ("repeat", from_index, times)
    for s in steps:
        if isinstance(s, Repeat):
            start = len(flat)
            flat += [("step", x) for x in s.steps]
            flat.append(("repeat", start, s.times))
        else:
            flat.append(("step", s))
    f = _Fit()
    created = int((datetime.now(timezone.utc) - FIT_EPOCH).total_seconds())
    f.msg(0, [(0, _ENUM, 5, 0), (1, _UINT16, 255, 0), (2, _UINT16, 0, 0), (3, _UINT32, serial & 0xFFFFFFFF, 0),
              (4, _UINT32, created, 0)])
    f.msg(26, [(4, _ENUM, 1, 0), (6, _UINT16, len(flat), 0), (8, _STRING, name, 32)])
    for i, item in enumerate(flat):
        if item[0] == "repeat":
            f.msg(27, [(254, _UINT16, i, 0), (0, _STRING, "Repeat", 16), (1, _ENUM, DUR_REPEAT, 0),
                       (2, _UINT32, item[1], 0), (3, _ENUM, TGT_OPEN, 0), (4, _UINT32, item[2], 0),
                       (5, _UINT32, 0, 0), (6, _UINT32, 0, 0), (7, _ENUM, ACTIVE, 0)])
            continue
        s = item[1]
        dtype, dval = ((DUR_DISTANCE, s.value * 100) if s.kind == "distance" else
                       (DUR_TIME, s.value * 1000) if s.kind == "time" else (DUR_OPEN, 0))
        if s.zone:
            lo, hi = _zone_bpm(s.zone, zones, max_hr)
            tgt = [(3, _ENUM, TGT_HR, 0), (4, _UINT32, 0, 0), (5, _UINT32, lo + 100, 0), (6, _UINT32, hi + 100, 0)]
        else:
            tgt = [(3, _ENUM, TGT_OPEN, 0), (4, _UINT32, 0, 0), (5, _UINT32, 0, 0), (6, _UINT32, 0, 0)]
        f.msg(27, [(254, _UINT16, i, 0), (0, _STRING, s.name or "Run", 16), (1, _ENUM, dtype, 0),
                   (2, _UINT32, dval, 0)] + tgt + [(7, _ENUM, s.intensity, 0)])
    return f.bytes()


# ---- reading Intervals.icu text back (for coach-written steps) -----------------------------

def parse_intervals(text: str) -> list | None:
    """Our own Intervals.icu workout text back into steps (for the FIT file)."""
    steps: list = []
    block: Repeat | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if block:
                steps.append(block)
                block = None
            continue
        m = re.match(r"^(\d+)x$", line)
        if m:
            if block:
                steps.append(block)
            block = Repeat(int(m.group(1)))
            continue
        m = re.match(rf"^-\s*(.*?)\s*{_NUM}(km|mtr|m|s)\b(?:\s+Z(\d)\s+HR)?\s*$", line)
        if not m:
            return None
        nm, n, unit, z = m.group(1).strip(), float(m.group(2)), m.group(3), m.group(4)
        kind, v = {"km": ("distance", n * 1000), "mtr": ("distance", n), "m": ("time", n * 60),
                   "s": ("time", n)}[unit]
        intensity = (WARMUP if nm.lower().startswith("warm") else COOLDOWN if nm.lower().startswith("cool")
                     else RECOVERY if nm.lower().startswith(("recover", "between")) else
                     INTERVAL if block else ACTIVE)
        st = Step(intensity, kind, v, int(z) if z else None, nm)
        (block.steps if block else steps).append(st)
    if block:
        steps.append(block)
    return steps or None


def for_session(target: dict) -> list | None:
    """The steps for one planned session: the coach's or Dex's steps, or a one-step easy run."""
    if target.get("steps"):
        return parse_intervals(target["steps"])
    if target.get("kind") in ("easy", "long") and target.get("km") and not target.get("hr_range"):
        return [Step(ACTIVE, "distance", float(target["km"]) * 1000, 2, "Easy")]
    return None


def file_for_session(w, runner) -> tuple[bytes, str]:
    """(FIT workout bytes, file name) for a planned_workouts row. Raises ValueError."""
    import json
    t = json.loads(w["target"] or "{}")
    steps = for_session(t)
    if not steps:
        raise ValueError("that session has no structured steps (it's a note on the calendar)")
    if not runner["hr_zones"]:
        raise ValueError("no heart rate zones on file")
    zones = [int(z) for z in json.loads(runner["hr_zones"])]
    name = re.split(r"\s+[—–-]\s+", w["description"])[0][:30] or "Run"
    data = to_fit(steps, name, zones, runner["max_hr"], serial=int(w["id"]))
    return data, f"{w['date']}-{re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower() or 'run'}.fit"
