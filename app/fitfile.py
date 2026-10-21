"""Read a Garmin/Coros/Suunto/Polar .fit file into a plain dict.

Only what the coaching stats need: summary values, per-second streams for runs, and the
strength sets a watch records. Nothing here touches the database.
"""
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import fitdecode

RUN_SPORTS = {"running"}
STRENGTH_SUB_SPORTS = {"strength_training"}
STRENGTH_SPORTS = {"training", "fitness_equipment"}


class FitError(ValueError):
    """The bytes are not a readable activity file."""


@dataclass
class FitSet:
    exercise: str
    set_no: int
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: int | None = None


@dataclass
class FitActivity:
    sport: str
    sub_sport: str
    start_utc: datetime | None
    local_date: str | None
    distance_m: float = 0.0
    timer_s: float = 0.0
    elapsed_s: float = 0.0
    avg_hr: float | None = None
    max_hr: float | None = None
    ascent_m: float | None = None
    temp_c: float | None = None
    time_s: list[float] = field(default_factory=list)      # seconds from start
    hr: list[float] = field(default_factory=list)
    speed: list[float] = field(default_factory=list)       # m/s
    sets: list[FitSet] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.sport in RUN_SPORTS:
            return "run"
        if self.sub_sport in STRENGTH_SUB_SPORTS or (self.sport in STRENGTH_SPORTS and self.sets):
            return "strength"
        return "other"

    @property
    def treadmill(self) -> bool:
        return self.sub_sport in ("treadmill", "indoor_running", "virtual_activity")


def _v(frame, name):
    try:
        return frame.get_value(name, fallback=None)
    except Exception:
        return None


def _num(x):
    if isinstance(x, (int, float)):
        return float(x)
    return None


def _label(x) -> str:
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        x = next((i for i in x if i not in (None, "unknown")), x[0] if x else "")
    return str(x).replace("_", " ").strip().lower()


def _utc(x) -> datetime | None:
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    return None


def parse(data: bytes) -> FitActivity:
    """Parse one .fit file. Raises FitError if it is not an activity file."""
    sessions, records, sets = [], [], []
    local_ts = None
    utc_ts = None
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.WARN) as fit:
            for frame in fit:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                name = frame.name
                if name == "session":
                    sessions.append(frame)
                elif name == "record":
                    records.append(frame)
                elif name == "set":
                    sets.append(frame)
                elif name == "activity":
                    local_ts = _v(frame, "local_timestamp")
                    utc_ts = _utc(_v(frame, "timestamp"))
    except Exception as e:  # fitdecode raises several types for corrupt input
        raise FitError(f"not a readable .fit file: {e}") from None
    if not sessions:
        raise FitError("no activity session in this file")

    s = sessions[0]
    start = _utc(_v(s, "start_time"))
    act = FitActivity(
        sport=_label(_v(s, "sport")).replace(" ", "_") or "unknown",
        sub_sport=_label(_v(s, "sub_sport")).replace(" ", "_") or "generic",
        start_utc=start,
        local_date=None,
        distance_m=sum(_num(_v(x, "total_distance")) or 0 for x in sessions),
        timer_s=sum(_num(_v(x, "total_timer_time")) or 0 for x in sessions),
        elapsed_s=sum(_num(_v(x, "total_elapsed_time")) or 0 for x in sessions),
        avg_hr=_num(_v(s, "avg_heart_rate")),
        max_hr=_num(_v(s, "max_heart_rate")),
        ascent_m=_num(_v(s, "total_ascent")),
        temp_c=_num(_v(s, "avg_temperature")),
    )

    # Local date: the activity message carries the watch's local time; otherwise use UTC.
    if start:
        offset = timedelta(0)
        if isinstance(local_ts, datetime) and utc_ts:
            offset = local_ts.replace(tzinfo=timezone.utc) - utc_ts
        act.local_date = (start + offset).date().isoformat()

    t0 = None
    for r in records:
        ts = _utc(_v(r, "timestamp"))
        if ts is None:
            continue
        t0 = t0 or ts
        hr = _num(_v(r, "heart_rate"))
        sp = _num(_v(r, "enhanced_speed"))
        if sp is None:
            sp = _num(_v(r, "speed"))
        act.time_s.append((ts - t0).total_seconds())
        act.hr.append(hr or 0.0)
        act.speed.append(sp or 0.0)
    if not any(act.hr):
        act.hr = []
    if act.max_hr is None and act.hr:
        act.max_hr = max(act.hr)

    n = 0
    for st in sets:
        if _label(_v(st, "set_type")) != "active":
            continue
        n += 1
        reps = _v(st, "repetitions")
        act.sets.append(FitSet(
            exercise=_label(_v(st, "category")) or "exercise",
            set_no=n,
            reps=int(reps) if isinstance(reps, (int, float)) else None,
            weight_kg=round(float(w), 2) if isinstance((w := _v(st, "weight")), (int, float)) else None,
            duration_s=int(d) if isinstance((d := _v(st, "duration")), (int, float)) else None,
        ))
    return act
