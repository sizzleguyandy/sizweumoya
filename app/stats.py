"""Deterministic coaching maths. No AI here: the agent only words the results."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

# --- HR zones -------------------------------------------------------------


def default_zones(max_hr: float) -> list[float]:
    """Upper bounds of Z1..Z4 as % of max HR (Z5 is above the last bound)."""
    return [round(max_hr * p) for p in (0.68, 0.78, 0.87, 0.93)]


def zone_of(hr: float, zones: list[float]) -> int:
    for i, upper in enumerate(zones):
        if hr < upper:
            return i + 1
    return 5


def time_in_zones(time_s: list[float], hr: list[float], zones: list[float]) -> list[int]:
    """Seconds in Z1..Z5 from aligned time and heart-rate streams."""
    out = [0.0] * 5
    for i in range(1, min(len(time_s), len(hr))):
        dt = time_s[i] - time_s[i - 1]
        if 0 < dt <= 30:  # ignore pauses
            out[zone_of(hr[i], zones) - 1] += dt
    return [int(round(x)) for x in out]


def easy_share(zone_s: list[int]) -> float:
    total = sum(zone_s)
    return (zone_s[0] + zone_s[1]) / total if total else 0.0


# --- HR drift ---------------------------------------------------------------


def hr_drift_pct(time_s: list[float], hr: list[float], velocity: list[float]) -> float | None:
    """Pace-adjusted cardiac drift: HR-per-speed, second half vs first half, in %."""
    n = min(len(time_s), len(hr), len(velocity))
    if n < 60:
        return None
    half = n // 2

    def ratio(a: int, b: int) -> float | None:
        pts = [(hr[i], velocity[i]) for i in range(a, b) if velocity[i] > 0.5 and hr[i] > 0]
        if len(pts) < 20:
            return None
        return mean(h for h, _ in pts) / mean(v for _, v in pts)

    r1, r2 = ratio(0, half), ratio(half, n)
    if not r1 or not r2:
        return None
    return round((r2 / r1 - 1) * 100, 1)


# --- Run classification -----------------------------------------------------


def classify_run(name: str, workout_type: int | None, distance_km: float,
                 zone_s: list[int], long_km: float = 16.0) -> str:
    """Strava workout_type for runs: 1 race, 2 long, 3 workout."""
    lname = (name or "").lower()
    if workout_type == 1 or "race" in lname or "parkrun" in lname:
        return "race"
    if workout_type == 3 or any(w in lname for w in ("interval", "tempo", "threshold", "fartlek", "reps")):
        return "workout"
    if workout_type == 2 or distance_km >= long_km:
        return "long"
    if sum(zone_s) and easy_share(zone_s) < 0.6:
        return "workout"
    return "easy"


def is_easy_valid(run_type: str, zone_s: list[int], elevation_m: float | None,
                  distance_km: float, temp_c: float | None) -> bool:
    """Qualifies for the fitness check: mostly Z2, not hilly, not hot."""
    if run_type not in ("easy", "long") or distance_km < 3:
        return False
    if easy_share(zone_s) < 0.7:
        return False
    if elevation_m is not None and distance_km and elevation_m / distance_km > 15:
        return False
    if temp_c is not None and temp_c > 26:
        return False
    return True


# --- Weekly mileage ---------------------------------------------------------


def km_change_pct(this_week: float, last_week: float) -> float | None:
    if last_week <= 0:
        return None
    return round((this_week / last_week - 1) * 100, 1)


def mileage_verdict(change_pct: float | None) -> str:
    if change_pct is None:
        return "no_baseline"
    if change_pct < -20:
        return "lighter"
    if change_pct <= 10:
        return "steady"
    return "big_jump"


# --- Fitness check ----------------------------------------------------------


@dataclass
class FitnessResult:
    verdict: str                 # fitter / steady / dipping / insufficient
    ref_hr: float | None = None
    pace_s_km: float | None = None
    prev_pace_s_km: float | None = None
    change_pct: float | None = None


def _pace_at_hr(runs: list[dict], ref_hr: float) -> float:
    """Average pace normalised to ref_hr. Assumes ~1% pace per 1% HR locally."""
    adjusted = [r["avg_pace_s_km"] * (r["avg_hr"] / ref_hr) for r in runs]
    return mean(adjusted)


def fitness_check(recent: list[dict], previous: list[dict], min_runs: int = 3) -> FitnessResult:
    """recent / previous: valid easy runs (avg_pace_s_km, avg_hr) for each 4-week window."""
    if len(recent) < min_runs or len(previous) < min_runs:
        return FitnessResult("insufficient")
    ref_hr = round(mean(r["avg_hr"] for r in recent + previous))
    now, before = _pace_at_hr(recent, ref_hr), _pace_at_hr(previous, ref_hr)
    change = round((before / now - 1) * 100, 1)  # positive = faster now
    verdict = "fitter" if change > 2 else "dipping" if change < -2 else "steady"
    return FitnessResult(verdict, ref_hr, round(now, 1), round(before, 1), change)


# --- Tip rules --------------------------------------------------------------


def run_tips(run: dict) -> list[str]:
    """Rule ids triggered by one run. The agent picks one and words it."""
    tips = []
    zone_s = [run.get(f"z{i}_s") or 0 for i in range(1, 6)]
    if run.get("run_type") == "easy" and sum(zone_s) and (1 - easy_share(zone_s)) > 0.30:
        tips.append("easy_too_hard")
    if (run.get("hr_drift_pct") or 0) > 5:
        tips.append("hr_drift")
    return tips


def week_tips(week_km: float, last_week_km: float, easy_pct: float,
              days_since_strength: int | None) -> list[str]:
    tips = []
    change = km_change_pct(week_km, last_week_km)
    if change is not None and change > 10:
        tips.append("volume_jump")
    if easy_pct and easy_pct < 0.70:
        tips.append("too_much_intensity")
    if days_since_strength is None or days_since_strength >= 7:
        tips.append("strength_gap")
    return tips


def format_pace(s_per_km: float | None) -> str:
    if not s_per_km:
        return "-"
    m, s = divmod(int(round(s_per_km)), 60)
    return f"{m}:{s:02d}/km"
