"""Structured workouts: Dex text -> Intervals.icu steps -> Garmin FIT workout file; zones pushed."""
import io
import json

import fitdecode
import httpx
import pytest

from app import intervals, programme, workouts as W

ZONES, MAX = [132, 144, 152, 162], 186


def test_dex_sessions_become_steps():
    t = W.to_intervals(W.from_dex("WU 2.0 km easy | 6 × 5 min @ T pace (4:24 /km) — 1 min rest | CD 1.0 km easy"))
    assert t == "- Warm up 2km Z2 HR\n\n6x\n- Hard 5m Z4 HR\n- Recover 1m\n\n- Cool down 1km Z2 HR"
    t = W.to_intervals(W.from_dex("WU 2.0 km easy | 6 × 800m @ I pace (4:03 /km) — 2 min jog | CD 1.0 km easy"))
    assert "6x\n- Hard 800mtr Z5 HR\n- Recover 2m" in t          # metres are "mtr": "m" is minutes
    t = W.to_intervals(W.from_dex("WU 2.0 km easy | 8 × 200m @ R pace (3:50 /km) — 200m jog recovery | CD 1.0 km easy"))
    assert "- Hard 200mtr\n- Recover 200mtr" in t                 # short reps: pace, no HR target
    t = W.to_intervals(W.from_dex("WU 2.0 km easy | 8 × 10–15 sec all-out uphill (6–10% grade) — walk/jog back "
                                  "down as full recovery | CD 1.5 km easy"))
    assert "8x\n- Hard 15s\n- Recover 90s" in t
    two = W.to_intervals(W.from_dex("WU 2.0 km easy | 2 sets of 6 × 200m @ R pace (3:50 /km) — 200m jog, 400m jog "
                                    "between sets | CD 1.0 km easy"))
    assert two.count("6x") == 2 and "- Between sets 400mtr" in two
    assert W.from_dex("WU 2.0 km easy | 4 × 200m R + 8 × 400m R + 4 × 200m R @ R pace (3:50 /km) | CD 1.0 km easy") is None
    assert W.from_dex("7.0 km easy") is None


def test_steps_survive_the_programme_file_round_trip():
    text = W.to_intervals(W.from_dex("WU 2.0 km easy | 5 × 2 min hard @ I pace — 1 min jog | CD 1.0 km easy"))
    assert programme.steps_text(text.replace("\n", "|")) == text        # blank lines close the repeat block
    assert W.to_intervals(W.parse_intervals(text)) == text


def _decode(data: bytes) -> list:
    out = []
    with fitdecode.FitReader(io.BytesIO(data)) as fr:
        for f in fr:
            if isinstance(f, fitdecode.FitDataMessage):
                out.append((f.name, {x.name: x.value for x in f.fields}))
    return out


def test_garmin_fit_workout_file_follows_the_spec():
    steps = W.from_dex("WU 2.0 km easy | 6 × 800m @ I pace (4:03 /km) — 2 min jog | CD 1.0 km easy")
    msgs = _decode(W.to_fit(steps, "Intervals 6x800", ZONES, MAX, serial=45))
    names = [m[0] for m in msgs]
    assert names[0] == "file_id" and names[1] == "workout" and set(names[2:]) == {"workout_step"}
    assert msgs[0][1]["type"] == "workout" and msgs[1][1]["num_valid_steps"] == 5 == len(names) - 2
    wu, hard, rec, rep, cd = (m[1] for m in msgs[2:])
    assert wu["intensity"] == "warmup" and wu["duration_distance"] == 2000
    assert (wu["custom_target_heart_rate_low"], wu["custom_target_heart_rate_high"]) == (233, 244)   # 133-144 bpm + 100
    assert hard["intensity"] == "interval" and hard["custom_target_heart_rate_low"] == 263          # Z5 from 163
    assert rec["duration_time"] == 120 and rec["target_type"] == "open"
    assert rep["duration_type"] == "repeat_until_steps_cmplt" and rep["duration_step"] == 1 and rep["repeat_steps"] == 6
    assert cd["intensity"] == "cooldown" and [m[1]["message_index"] for m in msgs[2:]] == [0, 1, 2, 3, 4]


def test_file_for_an_easy_session_and_a_note():
    runner = {"hr_zones": json.dumps(ZONES), "max_hr": MAX}
    w = {"id": 7, "date": "2026-11-03", "description": "Easy run", "target": json.dumps({"kind": "easy", "km": 5})}
    data, name = W.file_for_session(w, runner)
    assert name == "2026-11-03-easy-run.fit"
    step = [m for m in _decode(data) if m[0] == "workout_step"]
    assert len(step) == 1 and step[0][1]["duration_distance"] == 5000
    long_hm = {"id": 8, "date": "2026-11-08", "description": "Long run with HM effort",
               "target": json.dumps({"kind": "long", "km": 16, "hr_range": [152, 162]})}
    with pytest.raises(ValueError, match="no structured steps"):
        W.file_for_session(long_hm, runner)


def test_push_zones_writes_the_run_settings(monkeypatch):
    from app.admin import add_runner
    from app.db import connect, init_db
    c = connect(":memory:")
    init_db(c)
    rid, _, _ = add_runner(c, "Tom", "1", 186, None)
    c.execute("UPDATE runners SET hr_zones=?", (json.dumps(ZONES),))
    intervals.connect(c, rid, "i9", "a-key-for-tests-123")
    seen = []

    def handler(req):
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"sportSettings": [{"id": 5, "types": ["Ride"]}, {"id": 77, "types": ["Run"]}]})
        return httpx.Response(200, json={})
    monkeypatch.setattr(intervals, "_client", lambda link: httpx.Client(base_url="https://intervals.icu/api/v1",
                                                                        transport=httpx.MockTransport(handler)))
    out = intervals.push_zones(c, rid)
    assert out == {"zones": [132, 144, 152, 162, 186], "sport_settings_id": 77}
    put = seen[-1]
    assert put.method == "PUT" and put.url.path == "/api/v1/athlete/i9/sport-settings/77"
    assert json.loads(put.content)["hr_zones"] == [132, 144, 152, 162, 186]
