"""Hevy: workouts arrive by webhook and are stored as strength sessions.

Hevy POSTs {"workoutId": "..."} with the authorization header the person set in Hevy, and
expects 200 within 5 seconds. We answer at once, then fetch the workout with that person's
own API key (GET /v1/workouts/{id}, header "api-key") and store it like any other session.
Hevy's API is for Hevy Pro accounts and may change; the parser accepts missing fields.
"""
import logging
import secrets

import httpx

from . import plans
from .config import settings
from .identity import hash_token

log = logging.getLogger(__name__)

WARMUP = "warmup"


class HevyError(RuntimeError):
    pass


PAID_TIERS = ("full", "paid", "coach")


def connect(conn, rid: int, api_key: str) -> str:
    """Save a person's Hevy key. Returns the webhook secret to paste into Hevy (shown once).
    Hevy sync is a paid feature."""
    r = conn.execute("SELECT tier FROM runners WHERE runner_id=?", (rid,)).fetchone()
    if r is None or r["tier"] not in PAID_TIERS:
        raise HevyError("Hevy sync is part of the paid service; set the person's tier first")
    if not api_key or len(api_key) < 16:
        raise HevyError("that doesn't look like a Hevy API key")
    secret = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO hevy_links (runner_id, api_key, secret_hash) VALUES (?,?,?)"
                 " ON CONFLICT(runner_id) DO UPDATE SET api_key=excluded.api_key, secret_hash=excluded.secret_hash,"
                 " connected_at=datetime('now')", (rid, api_key.strip(), hash_token(secret)))
    conn.commit()
    return secret


def normalise(authorization: str | None) -> str:
    """The secret from an Authorization value, however it was pasted into Hevy: with or without
    "Bearer" (Hevy may add its own, giving "Bearer Bearer x"), quotes, or stray spaces."""
    token = (authorization or "").strip().strip("'\"").strip()
    while token.lower().startswith("bearer"):
        token = token[6:].lstrip(" :").strip().strip("'\"").strip()
    return token


def describe(authorization: str | None) -> str:
    """A safe description of a rejected header for the log: its shape, never its value."""
    raw = authorization or ""
    words = raw.split()
    quotes = any(q in raw for q in ("'", '"'))
    return (f"present={bool(raw)} words={len(words)} bearer_prefixes={raw.lower().count('bearer')} "
            f"token_len={len(normalise(raw))} quotes={quotes}")


def runner_for_secret(conn, authorization: str):
    """The person whose webhook secret this is, or None. Accepts "Bearer x" or "x"."""
    token = normalise(authorization)
    if len(token) < 16:
        return None
    return conn.execute("SELECT r.*, h.api_key FROM hevy_links h JOIN runners r ON r.runner_id=h.runner_id"
                        " WHERE h.secret_hash=? AND r.active=1", (hash_token(token),)).fetchone()


def fetch_workout(api_key: str, workout_id: str) -> dict:
    r = httpx.get(f"{settings.hevy_base_url}/v1/workouts/{workout_id}",
                  headers={"api-key": api_key, "accept": "application/json"}, timeout=20)
    if r.status_code != 200:
        raise HevyError(f"Hevy answered {r.status_code}")
    data = r.json()
    return data.get("workout", data) if isinstance(data, dict) else data


def list_workouts(api_key: str, page: int, page_size: int = 10) -> list[dict]:
    r = httpx.get(f"{settings.hevy_base_url}/v1/workouts", params={"page": page, "pageSize": page_size},
                  headers={"api-key": api_key, "accept": "application/json"}, timeout=30)
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        raise HevyError(f"Hevy answered {r.status_code}")
    return r.json().get("workouts", [])


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse(workout: dict) -> dict:
    """Hevy workout -> date, title, duration and working sets (warm-ups left out)."""
    start = workout.get("start_time") or workout.get("created_at") or ""
    end = workout.get("end_time") or ""
    from datetime import datetime
    duration = None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if end:
            duration = int((datetime.fromisoformat(end.replace("Z", "+00:00")) - a).total_seconds())
        local_date = a.astimezone().date().isoformat() if a.tzinfo else a.date().isoformat()
    except ValueError:
        raise HevyError("workout has no usable start time") from None
    sets = []
    for ex in workout.get("exercises") or []:
        name = (ex.get("title") or "exercise").strip().lower()
        n = 0
        for st in ex.get("sets") or []:
            if (st.get("type") or "normal") == WARMUP:
                continue
            n += 1
            reps = st.get("reps")
            sets.append({"exercise": name, "set_no": n, "reps": int(reps) if reps is not None else None,
                         "weight_kg": _num(st.get("weight_kg")),
                         "duration_s": int(st["duration_seconds"]) if st.get("duration_seconds") else None,
                         "distance_m": _num(st.get("distance_meters")), "rpe": _num(st.get("rpe"))})
    return {"id": str(workout.get("id")), "date": local_date, "title": workout.get("title") or "Hevy workout",
            "note": (workout.get("description") or "")[:500] or None, "duration_s": duration,
            "updated_at": workout.get("updated_at"), "sets": sets}


def replace_manual_sessions(conn, rid: int, day: str) -> int:
    """Hevy has the full detail, so a session typed to Sarah or imported from a spreadsheet for
    the same day is the same workout recorded twice: remove it (its sets go with it)."""
    n = conn.execute("DELETE FROM strength_sessions WHERE runner_id=? AND date=? AND source IN ('sheet','text')",
                     (rid, day)).rowcount
    conn.commit()
    return n


def store(conn, runner, workout: dict) -> dict:
    """Store (or replace, if edited in Hevy) one workout. Key: hevy:<workout id>."""
    w = parse(workout)
    key = f"hevy:{w['id']}"
    replaced = replace_manual_sessions(conn, runner["runner_id"], w["date"])
    old = conn.execute("SELECT id, raw_description FROM strength_sessions WHERE activity_key=?", (key,)).fetchone()
    if old:
        if old["raw_description"] == (w["updated_at"] or ""):
            return {"status": "duplicate", "date": w["date"]}
        conn.execute("DELETE FROM strength_sessions WHERE id=?", (old["id"],))
    sid = conn.execute(
        "INSERT INTO strength_sessions (runner_id, activity_key, source, date, title, note, duration_s,"
        " raw_description, parse_ok) VALUES (?,?,?,?,?,?,?,?,1)",
        (runner["runner_id"], key, "hevy", w["date"], w["title"][:120], w["note"], w["duration_s"],
         w["updated_at"] or "")).lastrowid
    conn.executemany(
        "INSERT INTO strength_sets (session_id, runner_id, exercise, set_no, reps, weight_kg, duration_s, distance_m)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(sid, runner["runner_id"], s["exercise"], s["set_no"], s["reps"], s["weight_kg"], s["duration_s"],
          s["distance_m"]) for s in w["sets"]])
    conn.commit()
    plan = plans.match_strength(conn, runner["runner_id"], w["date"], key)
    return {"status": "updated" if old else "stored", "replaced_manual": replaced, "date": w["date"], "title": w["title"],
            "sets": len(w["sets"]), "exercises": len({s["exercise"] for s in w["sets"]}), "plan": plan}
