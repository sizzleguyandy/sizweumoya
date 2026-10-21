"""Minimal FIT encoder for tests: builds real .fit bytes that fitdecode can read.

Covers file_id, record, session, activity and set messages with the fields app.fitfile reads.
"""
import struct
from datetime import datetime, timezone

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

# base types
ENUM, UINT8, UINT16, UINT32 = 0x00, 0x02, 0x84, 0x86
SIZES = {ENUM: 1, UINT8: 1, UINT16: 2, UINT32: 4}
FMT = {ENUM: "B", UINT8: "B", UINT16: "H", UINT32: "I"}

SPORT = {"running": 1, "training": 10}
SUB_SPORT = {"generic": 0, "treadmill": 1, "strength_training": 20}
CATEGORY = {"bench_press": 0, "calf_raise": 1, "deadlift": 8, "lunge": 17, "squat": 28}


def _crc(data: bytes) -> int:
    table = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    crc = 0
    for byte in data:
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[byte & 0xF]
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[(byte >> 4) & 0xF]
    return crc


def _ts(dt: datetime) -> int:
    return int((dt - FIT_EPOCH).total_seconds())


class _Writer:
    def __init__(self):
        self.body = bytearray()
        self.defs = {}

    def msg(self, local: int, global_num: int, fields: list[tuple[int, int, int]]):
        """fields: (field_num, base_type, value)."""
        key = (local, global_num, tuple((f, b) for f, b, _ in fields))
        if self.defs.get(local) != key:
            self.body += struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
            for f, b, _ in fields:
                self.body += struct.pack("<BBB", f, SIZES[b], b)
            self.defs[local] = key
        self.body += struct.pack("<B", local)
        for _, b, v in fields:
            self.body += struct.pack("<" + FMT[b], v)

    def bytes(self) -> bytes:
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self.body), b".FIT")
        header += struct.pack("<H", _crc(header))
        data = header + bytes(self.body)
        return data + struct.pack("<H", _crc(data))


def run_fit(start: datetime, seconds: int = 1800, speed_mps: float = 3.0, hr=140,
            sub_sport: str = "generic", tz_offset_h: int = 2) -> bytes:
    """A run: one record per second. hr may be a number or a callable(second) -> bpm."""
    w = _Writer()
    st = _ts(start)
    w.msg(0, 0, [(0, ENUM, 4), (1, UINT16, 1), (4, UINT32, st)])
    dist = 0.0
    hrs = []
    for i in range(seconds + 1):
        h = int(hr(i) if callable(hr) else hr)
        hrs.append(h)
        w.msg(1, 20, [(253, UINT32, st + i), (3, UINT8, h), (5, UINT32, int(dist * 100)),
                      (6, UINT16, int(speed_mps * 1000))])
        dist += speed_mps
    total = speed_mps * seconds
    w.msg(2, 18, [(253, UINT32, st + seconds), (2, UINT32, st), (5, ENUM, SPORT["running"]),
                  (6, ENUM, SUB_SPORT[sub_sport]), (7, UINT32, seconds * 1000),
                  (8, UINT32, seconds * 1000), (9, UINT32, int(total * 100)),
                  (16, UINT8, int(sum(hrs) / len(hrs))), (17, UINT8, max(hrs)), (22, UINT16, 20)])
    w.msg(3, 34, [(253, UINT32, st + seconds), (5, UINT32, st + seconds + tz_offset_h * 3600)])
    return w.bytes()


def strength_fit(start: datetime, sets: list[tuple[str, int, float]], tz_offset_h: int = 2) -> bytes:
    """sets: (category, reps, weight_kg). A rest set is written between active sets."""
    w = _Writer()
    st = _ts(start)
    w.msg(0, 0, [(0, ENUM, 4), (1, UINT16, 1), (4, UINT32, st)])
    t = st
    for i, (cat, reps, kg) in enumerate(sets):
        w.msg(4, 225, [(254, UINT32, t), (0, UINT32, 40_000), (3, UINT16, reps),
                       (4, UINT16, int(kg * 16)), (5, UINT8, 1), (7, UINT16, CATEGORY[cat]),
                       (10, UINT16, 2 * i)])
        t += 40
        w.msg(4, 225, [(254, UINT32, t), (0, UINT32, 90_000), (3, UINT16, 0), (4, UINT16, 0),
                       (5, UINT8, 0), (7, UINT16, 65534), (10, UINT16, 2 * i + 1)])
        t += 90
    dur = t - st
    w.msg(2, 18, [(253, UINT32, t), (2, UINT32, st), (5, ENUM, SPORT["training"]),
                  (6, ENUM, SUB_SPORT["strength_training"]), (7, UINT32, dur * 1000),
                  (8, UINT32, dur * 1000), (9, UINT32, 0), (16, UINT8, 110), (17, UINT8, 150),
                  (22, UINT16, 0)])
    w.msg(3, 34, [(253, UINT32, t), (5, UINT32, t + tz_offset_h * 3600)])
    return w.bytes()
