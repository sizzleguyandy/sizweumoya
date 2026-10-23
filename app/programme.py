"""Load a programme Coach Andy wrote (run, strength, or both) from a simple CSV.

One row per session:

  date,type,description,km,kind,hr_low,hr_high,exercises,steps
  2026-11-03,run,Easy run,5,easy,,144,
  2026-11-05,run,Hill reps 6x90s,7,workout,160,175,,"- 15m Z2 HR|6x|- 90s Z4 HR|- 90s Z1 HR|- 10m Z2 HR"
  2026-11-04,strength,Lower body A,,,,,"Squat 4x6 70kg; RDL 3x8 60kg; Calf raise 3x15"

type: run or strength. kind (runs): easy, long, workout, benchmark, race. Empty cells are fine.
Loading a programme moves the person to "guided" for that discipline until its last date.
"""
import csv
import json
from datetime import date


class ProgrammeError(ValueError):
    pass


def load_csv(conn, rid: int, path: str, replace: bool = False) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows_ = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    if not rows_:
        raise ProgrammeError("the file has no sessions")
    by_type: dict[str, list[dict]] = {}
    for i, r in enumerate(rows_, start=2):
        try:
            d = date.fromisoformat(r.get("date", ""))
        except ValueError:
            raise ProgrammeError(f"line {i}: date must be YYYY-MM-DD") from None
        t = r.get("type", "run") or "run"
        if t not in ("run", "strength"):
            raise ProgrammeError(f"line {i}: type must be run or strength")
        if not r.get("description"):
            raise ProgrammeError(f"line {i}: description is empty")
        target: dict = {}
        if r.get("km"):
            target["km"] = float(r["km"])
        target["kind"] = r.get("kind") or ("strength" if t == "strength" else "easy")
        if r.get("hr_low") and r.get("hr_high"):
            target["hr_range"] = [int(r["hr_low"]), int(r["hr_high"])]
        elif r.get("hr_high"):
            target["hr_max"] = int(r["hr_high"])
        if r.get("steps"):
            # Structured workout for the watch, in Intervals.icu's workout text; "|" starts a new line.
            target["steps"] = "\n".join(x.strip() for x in r["steps"].split("|") if x.strip())
        if r.get("exercises"):
            target["exercises"] = [e.strip() for e in r["exercises"].split(";") if e.strip()]
        by_type.setdefault(t, []).append({"date": d.isoformat(), "description": r["description"][:300],
                                          "target": target})
    out = {}
    for t, sessions in by_type.items():
        if replace:
            conn.execute("UPDATE plans SET active=0 WHERE runner_id=? AND type=? AND active=1", (rid, t))
        elif conn.execute("SELECT 1 FROM plans WHERE runner_id=? AND type=? AND active=1 AND end_date>=date('now')",
                          (rid, t)).fetchone():
            raise ProgrammeError(f"a {t} programme is already active; use --replace to end it first")
        sessions.sort(key=lambda s: s["date"])
        pid = conn.execute("INSERT INTO plans (runner_id, type, start_date, end_date) VALUES (?,?,?,?)",
                           (rid, t, sessions[0]["date"], sessions[-1]["date"])).lastrowid
        conn.executemany("INSERT INTO planned_workouts (plan_id, runner_id, date, description, target)"
                         " VALUES (?,?,?,?,?)",
                         [(pid, rid, s["date"], s["description"], json.dumps(s["target"])) for s in sessions])
        out[t] = {"plan_id": pid, "sessions": len(sessions), "from": sessions[0]["date"], "to": sessions[-1]["date"]}
    conn.commit()
    return out
