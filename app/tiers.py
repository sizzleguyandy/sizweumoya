"""What each plan includes.

free       everything except a programme: log runs and gym sessions (watch, Intervals.icu,
           Hevy, files, typed), feedback after every session, weekly and monthly reports,
           trends, paces and zones, TRUE PACE, races, chat. Sarah works in monitor mode.
programme  free, plus a strength and/or race programme from Coach Andy (written with Dex),
           the programme form, the race-day pacing plan, and the programme on their watch.

Older tier names ("full", "paid", "coach") count as programme.
"""

FREE = "free"
PROGRAMME = "programme"
TIERS = (FREE, PROGRAMME)
PROGRAMME_TIERS = (PROGRAMME, "full", "paid", "coach")
UPGRADE_NOTE = ("programmes are part of the upgrade (a strength and/or race programme from Coach Andy); "
                "on the free plan they log their own training and get feedback")


class TierError(ValueError):
    pass


def tier(conn, rid: int) -> str:
    r = conn.execute("SELECT tier FROM runners WHERE runner_id=?", (rid,)).fetchone()
    return PROGRAMME if r and r["tier"] in PROGRAMME_TIERS else FREE


def has_programmes(conn, rid: int) -> bool:
    return tier(conn, rid) == PROGRAMME


def require_programmes(conn, rid: int) -> None:
    if not has_programmes(conn, rid):
        name = conn.execute("SELECT name FROM runners WHERE runner_id=?", (rid,)).fetchone()
        raise TierError(f"{name['name'] if name else 'This person'} is on the free plan: {UPGRADE_NOTE}. "
                        "Upgrade first: python -m app.admin set-tier --runner <id> --tier programme")


def info(conn, rid: int) -> dict:
    t = tier(conn, rid)
    return {"plan": t, "includes_programmes": t == PROGRAMME,
            "upgrade": None if t == PROGRAMME else UPGRADE_NOTE}
