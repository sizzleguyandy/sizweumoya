"""The Dex programme form: a Google Form Coach Andy makes from QUESTIONS (docs/DEX_FORM.md).

The runner fills it in with their TR3D code; Coach Andy downloads the responses as CSV and
imports them (`python -m app.admin dex-intake --file responses.csv`). Each answer is checked,
saved, and used by Dex: race (from the race library or their own), days, build speed,
fitness when there's no time trial on file, Couch to 5K, and whether they want a race-day
pacing plan. Column headers are the question titles exactly as below.
"""
import csv
import json
import re
import secrets
from datetime import date, datetime

from . import queries, racelib

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OTHER_RACE = "Another race (not in this list)"
GOALS = {"A race": "race", "Couch to 5K (I'm new to running)": "c25k"}
BUILDS = {"Steady (safer; best if you're newer or injury-prone)": "conservative",
          "Faster (experienced, consistent runners)": "aggressive"}
RECENT = {"5 km": 5.0, "10 km": 10.0, "Half marathon (21.1 km)": 21.0975, "Marathon (42.2 km)": 42.195,
          "None in the last 6 months": None}


def race_options() -> list[str]:
    return [r["name"] for r in racelib.all_races()] + [OTHER_RACE]


# (key, question title, form type, options or None, required)
QUESTIONS = [
    ("code", "Your TR3D code", "Short answer", None, True),
    ("name", "Your name", "Short answer", None, True),
    ("goal", "What is this programme for?", "Multiple choice", list(GOALS), True),
    ("race", "Which race?", "Dropdown", "race library", False),
    ("race_date", "Race date", "Date", None, False),
    ("race_name", "If another race: its name", "Short answer", None, False),
    ("race_km", "If another race: distance in km", "Short answer (number)", None, False),
    ("race_climb", "If another race: total climb in metres (if you know it)", "Short answer (number)", None, False),
    ("recent", "Your most recent race or time trial", "Multiple choice", list(RECENT), True),
    ("recent_time", "Its finish time (h:mm:ss or mm:ss)", "Short answer", None, False),
    ("recent_date", "Date of that race or time trial", "Date", None, False),
    ("weekly_km", "How many km do you run in a normal week right now?", "Short answer (number)", None, True),
    ("longest_km", "Your longest run in the last month, in km", "Short answer (number)", None, True),
    ("run_days", "Which days can you run?", "Checkboxes", DAYS, True),
    ("long_day", "Best day for your long run", "Dropdown", DAYS, True),
    ("quality_day", "Best day for a harder session", "Dropdown", DAYS, True),
    ("build", "How fast should your training build?", "Multiple choice", list(BUILDS), True),
    ("race_pacing", "Would you like a personal race-day pacing plan?", "Multiple choice", ["Yes", "No"], True),
    ("location", "Where do you usually run? (town or city)", "Short answer", None, False),
    ("injuries", "Any injuries, niggles or health conditions Coach Andy should know about?", "Paragraph", None, False),
    ("notes", "Anything else Coach Andy should know?", "Paragraph", None, False),
]
TITLES = {t.lower(): k for k, t, *_ in QUESTIONS}


class IntakeError(ValueError):
    pass


# ---- codes ---------------------------------------------------------------------------------

def code_for(conn, rid: int) -> str:
    r = conn.execute("SELECT intake_code FROM runners WHERE runner_id=?", (rid,)).fetchone()
    if r and r["intake_code"]:
        return r["intake_code"]
    while True:
        code = "TR3D-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
        if not conn.execute("SELECT 1 FROM runners WHERE intake_code=?", (code,)).fetchone():
            break
    conn.execute("UPDATE runners SET intake_code=? WHERE runner_id=?", (code, rid))
    conn.commit()
    return code


# ---- reading one response ------------------------------------------------------------------

def _num(v: str, what: str, lo: float, hi: float) -> float | None:
    v = (v or "").strip().replace(",", ".")
    if not v:
        return None
    m = re.search(r"\d+(?:\.\d+)?", v)
    if not m or not lo <= float(m.group()) <= hi:
        raise IntakeError(f"{what}: {v!r} isn't a number between {lo:g} and {hi:g}")
    return float(m.group())


def _date(v: str, what: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    raise IntakeError(f"{what}: {v!r} isn't a date")


def _time_s(v: str) -> int | None:
    v = (v or "").strip()
    if not v:
        return None
    parts = re.split(r"[:.]", v)
    if not all(p.isdigit() for p in parts) or not 2 <= len(parts) <= 3:
        raise IntakeError(f"finish time {v!r} should look like 25:30 or 1:52:10")
    n = [int(p) for p in parts]
    return n[0] * 3600 + n[1] * 60 + n[2] if len(n) == 3 else n[0] * 60 + n[1]


def _pick(v: str, options: dict, what: str):
    v = (v or "").strip()
    for k, out in options.items():
        if v.lower() == k.lower():
            return out
    raise IntakeError(f"{what}: {v!r} isn't one of the form's options")


def _day(v: str) -> str:
    v = (v or "").strip()[:3].capitalize()
    if v not in [d[:3] for d in DAYS]:
        raise IntakeError(f"{v!r} isn't a day")
    return v


def _stamp(ts: str) -> str:
    """Google Forms' Timestamp column (in the sheet's locale) -> ISO date and time."""
    ts = (ts or "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %I:%M:%S %p", "%d/%m/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(ts, fmt).isoformat(timespec="seconds")
        except ValueError:
            pass
    return datetime.now().isoformat(timespec="seconds")


def parse(raw: dict) -> dict:
    """One CSV row (question title -> answer) -> checked answers. Raises IntakeError."""
    a = {TITLES[k.strip().lower()]: (v or "").strip() for k, v in raw.items() if k and k.strip().lower() in TITLES}
    ts = next((v for k, v in raw.items() if k and k.strip().lower() == "timestamp"), "")
    missing = [t for k, t, _, _, req in QUESTIONS if req and not a.get(k)]
    if missing:
        raise IntakeError("missing answers: " + "; ".join(missing))
    out = {"code": a["code"].upper().replace(" ", ""), "name": a["name"], "submitted_at": _stamp(ts),
           "goal": _pick(a["goal"], GOALS, "goal"), "build": _pick(a["build"], BUILDS, "build"),
           "race_pacing": a["race_pacing"].lower().startswith("y"),
           "weekly_km": _num(a["weekly_km"], "weekly km", 0, 250), "longest_km": _num(a["longest_km"], "longest run", 0, 120),
           "location": a.get("location") or None, "injuries": a.get("injuries") or None, "notes": a.get("notes") or None}
    days = [_day(d) for d in re.split(r"[,;]\s*", a["run_days"]) if d.strip()]
    out["long_day"], out["quality_day"] = _day(a["long_day"]), _day(a["quality_day"])
    if out["long_day"] == out["quality_day"]:
        raise IntakeError("the long run and the harder session must be on different days")
    out["extra_days"] = [d for d in days if d not in (out["long_day"], out["quality_day"])]
    if out["goal"] == "race":
        choice = a.get("race") or ""
        out["race_date"] = _date(a.get("race_date"), "race date")
        if choice and choice != OTHER_RACE:
            lib = next((r for r in racelib.all_races() if r["name"].lower() == choice.lower()), None)
            if lib is None:
                raise IntakeError(f"race {choice!r} isn't in the race library")
            out["race"] = {"name": lib["name"], "preset_id": lib["id"], "distance_km": lib["distance_km"],
                           "climb_m": lib["climb_m"], "date": out["race_date"] or lib["usual_date"],
                           "date_from": "form" if out["race_date"] else "library (usual date)"}
        else:
            km = _num(a.get("race_km"), "race distance", 1, 250)
            if not a.get("race_name") or not km or not out["race_date"]:
                raise IntakeError("for another race give its name, distance and date")
            out["race"] = {"name": a["race_name"], "preset_id": None, "distance_km": km,
                           "climb_m": _num(a.get("race_climb"), "race climb", 0, 10000),
                           "date": out["race_date"], "date_from": "form"}
    recent_km = _pick(a["recent"], RECENT, "recent race")
    if recent_km:
        t = _time_s(a.get("recent_time"))
        if not t:
            raise IntakeError("give the finish time of the recent race or time trial")
        out["recent"] = {"distance_km": recent_km, "time_s": t, "date": _date(a.get("recent_date"), "recent race date")}
    return out


# ---- import ----------------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def store(conn, row: dict) -> dict:
    """Check one response, match it to a runner by code and save it, with its race, location and
    health notes. Returns what happened."""
    ans = parse(row)
    r = conn.execute("SELECT * FROM runners WHERE intake_code=?", (ans["code"],)).fetchone()
    if r is None:
        raise IntakeError(f"no runner has the code {ans['code']}")
    rid = r["runner_id"]
    cur = conn.execute("INSERT OR IGNORE INTO dex_intake (runner_id, submitted_at, answers) VALUES (?,?,?)",
                       (rid, ans["submitted_at"], json.dumps(ans)))
    if not cur.rowcount:
        return {"runner": r["name"], "status": "already imported"}
    done = []
    if ans.get("race"):
        from . import onboarding
        rc = ans["race"]
        if date.fromisoformat(rc["date"]) > date.today():
            onboarding.save_race(conn, rid, rc["name"], rc["date"], rc["distance_km"], ascent_m=rc["climb_m"],
                                 preset_id=rc["preset_id"])
            done.append(f"race {rc['name']} {rc['date']}")
    if ans.get("injuries"):
        queries.add_note(conn, rid, "health", f"Programme form: {ans['injuries']}"[:500])
        done.append("health note")
    if ans.get("location") and r["latitude"] is None:
        try:
            from . import truepace
            p = truepace.find_place(ans["location"])
            conn.execute("UPDATE runners SET location_name=?, latitude=?, longitude=?, elevation_m=? WHERE runner_id=?",
                         (f"{p['name']}, {p['country']}" if p["country"] else p["name"], p["latitude"],
                          p["longitude"], p["elevation_m"], rid))
            done.append(f"location {p['name']}")
        except Exception:
            done.append("location not found")
    conn.commit()
    return {"runner": r["name"], "runner_id": rid, "status": "imported", "goal": ans["goal"], "saved": done,
            "injuries": ans.get("injuries")}


def import_rows(conn, rows: list[dict]) -> list[dict]:
    out = []
    for i, row in enumerate(rows, start=2):
        try:
            out.append({"row": i, **store(conn, row)})
        except IntakeError as e:
            out.append({"row": i, "status": "error", "error": str(e)})
    return out


def latest(conn, rid: int) -> dict | None:
    r = conn.execute("SELECT answers FROM dex_intake WHERE runner_id=? ORDER BY submitted_at DESC, id DESC LIMIT 1",
                     (rid,)).fetchone()
    return json.loads(r["answers"]) if r else None


def form_markdown() -> str:
    """The form, question by question, for Coach Andy to build in Google Forms."""
    lines = []
    for n, (key, title, kind, opts, req) in enumerate(QUESTIONS, start=1):
        lines.append(f"{n}. **{title}** ({kind}{', required' if req else ''})")
        if opts == "race library":
            opts = race_options()
        for o in opts or []:
            lines.append(f"   - {o}")
    return "\n".join(lines)
