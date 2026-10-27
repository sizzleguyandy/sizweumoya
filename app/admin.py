"""Admin CLI.

  python -m app.admin init
  python -m app.admin add-runner --name Andrew --chat-id 123456789 --max-hr 188 --goal "Sub-2 half"
  python -m app.admin set-hermes-key --runner 1 --key <API_SERVER_KEY of that profile>
  python -m app.admin coach-token
  python -m app.admin delete-runner --runner 1
  python -m app.admin reset-runner --runner 1 --name Andy --tier full
  python -m app.admin set-zones --runner 1 --zones 132,144,152,162 [--max-hr 184] [--easy-cap 138]
  python -m app.admin onboard --name Tom --chat-id 123456789 [--focus running|strength|hybrid]
  python -m app.admin set-focus --runner 2 --focus hybrid
  python -m app.admin welcome --runner 2
  python -m app.admin buttons --runner 2
  python -m app.admin load-programme --runner 2 --file programme.csv [--replace]
  python -m app.admin export-record --runner 2 [--to path.db]
  python -m app.admin offboard --runner 2 [--keep-copy path.db]
  INTERVALS_API_KEY=... python -m app.admin intervals-connect --runner 1 --athlete-id i12345
  python -m app.admin dex-draft --runner 2 [--race "Two Oceans"] [--extra-days Thu,Sat] [--propose]
  python -m app.admin intervals-set-coach --runner 1        # Coach Andy's own key coaches everyone else
  python -m app.admin intervals-link --runner 2 --athlete-id i23456   # athlete accepted Coach Andy as coach
  python -m app.admin intervals-check --runner 2
  python -m app.admin intervals-push --runner 1 [--days 28] [--dry-run]
  python -m app.admin set-steps --runner 1 --date 2026-10-29 --steps "- 15m Z2 HR|4x|- 3m Z4 HR|- 3m Z1 HR|- 10m Z2 HR"
  python -m app.admin intervals-pull --runner 1 --since 2026-10-01
  python -m app.admin garmin-link --runner 1 --user-id <Garmin userId>
  HEVY_API_KEY=... python -m app.admin hevy-connect --runner 1
  python -m app.admin hevy-import --runner 1 --since 2026-08-01
  python -m app.admin move-session --runner 1 --from 2026-10-23 --to 2026-10-24
  python -m app.admin purge-strava
  python -m app.admin import-tracker --runner 1 --file tracker.xlsx [--replace]
"""
import argparse
import json

from . import garmin, hermes, intervals, hevy, keyboard, onboarding, programme, record, stats, tracker_import
from .config import settings
from .db import connect, init_db
from .identity import hash_token, new_token


def add_runner(conn, name: str, chat_id: str, max_hr: int | None, goal: str | None) -> tuple[int, str, str]:
    token = new_token()
    cur = conn.execute(
        "INSERT INTO runners (name, telegram_chat_id, hermes_profile, mcp_token_hash, max_hr, hr_zones, goal)"
        " VALUES (?,?,?,?,?,?,?)",
        (name, chat_id, "pending", hash_token(token), max_hr,
         json.dumps(stats.default_zones(max_hr)) if max_hr else None, goal),
    )
    rid = cur.lastrowid
    profile = f"runner-{rid}"
    conn.execute("UPDATE runners SET hermes_profile=? WHERE runner_id=?", (profile, rid))
    conn.commit()
    return rid, profile, token


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    for name in ("add-runner", "onboard"):
        a = sub.add_parser(name)
        a.add_argument("--name", required=True)
        a.add_argument("--chat-id", required=True)
        a.add_argument("--max-hr", type=int)
        a.add_argument("--goal")
        a.add_argument("--focus", choices=["running", "strength", "hybrid"])
    fo = sub.add_parser("set-focus")
    fo.add_argument("--runner", type=int, required=True)
    fo.add_argument("--focus", required=True, choices=["running", "strength", "hybrid"])
    bt = sub.add_parser("buttons", help="(re)send the shortcut buttons to a person's chat")
    bt.add_argument("--runner", type=int, required=True)
    w = sub.add_parser("welcome", help="Sarah sends the welcome and starts onboarding")
    w.add_argument("--runner", type=int, required=True)
    lp = sub.add_parser("load-programme", help="load a programme from the Excel template (or a CSV)")
    lp.add_argument("--runner", type=int, required=True)
    lp.add_argument("--file", required=True)
    lp.add_argument("--replace", action="store_true", help="end the current programme of the same type first")
    ex = sub.add_parser("export-record")
    ex.add_argument("--runner", type=int, required=True)
    ex.add_argument("--to")
    o = sub.add_parser("offboard", help="delete a runner and everything held for them")
    o.add_argument("--runner", type=int, required=True)
    o.add_argument("--keep-copy", help="save a final copy of their record here first (e.g. if they asked for it)")
    k = sub.add_parser("set-hermes-key")
    k.add_argument("--runner", type=int, required=True)
    k.add_argument("--key", required=True)
    sub.add_parser("coach-token")
    d = sub.add_parser("delete-runner")
    d.add_argument("--runner", type=int, required=True)
    r = sub.add_parser("reset-runner")
    r.add_argument("--runner", type=int, required=True)
    r.add_argument("--name")
    r.add_argument("--tier")
    z = sub.add_parser("set-zones", help="Coach Andy's override: locks the zones")
    z.add_argument("--runner", type=int, required=True)
    z.add_argument("--zones", required=True, help="upper bounds of Z1-Z4, e.g. 132,144,152,162")
    z.add_argument("--max-hr", type=int)
    z.add_argument("--easy-cap", type=int)
    ic = sub.add_parser("intervals-connect", help="link Intervals.icu (key read from INTERVALS_API_KEY)")
    ic.add_argument("--runner", type=int, required=True)
    ic.add_argument("--athlete-id", required=True)
    dx = sub.add_parser("dex-draft", help="Dex, the run brain: draft a running programme for Coach Andy")
    dx.add_argument("--runner", type=int, required=True)
    dx.add_argument("--race", default="", help="part of the race name (default: their next race)")
    dx.add_argument("--weekly-km", type=float)
    dx.add_argument("--vdot", type=float)
    dx.add_argument("--long-day", default="Sun")
    dx.add_argument("--quality-day", default="Tue")
    dx.add_argument("--extra-days", default="Thu", help='e.g. "Thu,Sat"')
    dx.add_argument("--profile", default="conservative", choices=["conservative", "aggressive"])
    dx.add_argument("--start", help="YYYY-MM-DD, default next Monday")
    dx.add_argument("--propose", action="store_true", help="send it to Coach Andy as an approval")
    dd = sub.add_parser("dedupe-runs", help="find runs stored twice from different files (--apply removes them)")
    dd.add_argument("--runner", type=int, required=True)
    dd.add_argument("--apply", action="store_true")
    isc = sub.add_parser("intervals-set-coach", help="mark this person's own Intervals.icu key as Coach Andy's")
    isc.add_argument("--runner", type=int, required=True)
    ilk = sub.add_parser("intervals-link", help="link an athlete who accepted Coach Andy as coach (no key)")
    ilk.add_argument("--runner", type=int, required=True)
    ilk.add_argument("--athlete-id", required=True)
    ick = sub.add_parser("intervals-check", help="show a person's Intervals.icu link and try it now")
    ick.add_argument("--runner", type=int, required=True)
    ip = sub.add_parser("intervals-push", help="send upcoming programme sessions to the Intervals.icu calendar")
    ip.add_argument("--runner", type=int, required=True)
    ip.add_argument("--days", type=int, default=28)
    ip.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    ss = sub.add_parser("set-steps", help="Coach Andy: structured watch steps for one planned session")
    ss.add_argument("--runner", type=int, required=True)
    ss.add_argument("--date", required=True)
    ss.add_argument("--steps", required=True, help='Intervals.icu workout text, lines separated by "|"')
    il = sub.add_parser("intervals-pull", help="import activities and wellness from Intervals.icu")
    il.add_argument("--runner", type=int, required=True)
    il.add_argument("--since", required=True)
    gl = sub.add_parser("garmin-link", help="link a person to their Garmin userId (for testing with the "
                                            "User Authorization tool, before the OAuth flow exists)")
    gl.add_argument("--runner", type=int, required=True)
    gl.add_argument("--user-id", required=True)
    hc = sub.add_parser("hevy-connect", help="link a person's Hevy account (key read from HEVY_API_KEY)")
    hc.add_argument("--runner", type=int, required=True)
    hc.add_argument("--public-url", default="https://coach.sizweumoya.com")
    hi = sub.add_parser("hevy-import", help="import past Hevy workouts")
    hi.add_argument("--runner", type=int, required=True)
    hi.add_argument("--since", required=True, help="YYYY-MM-DD")
    mv = sub.add_parser("move-session", help="Coach Andy: move one programme session to another day")
    mv.add_argument("--runner", type=int, required=True)
    mv.add_argument("--from", dest="from_date", required=True)
    mv.add_argument("--to", dest="to_date", required=True)
    sub.add_parser("purge-strava", help="delete anything left from the old Strava version")
    t = sub.add_parser("import-tracker")
    t.add_argument("--runner", type=int, required=True)
    t.add_argument("--file", required=True)
    t.add_argument("--replace", action="store_true", help="clear the runner's existing data first")
    args = p.parse_args()

    conn = connect()
    init_db(conn)
    if args.cmd == "init":
        print(f"Initialised {settings.db_path}")
    elif args.cmd in ("add-runner", "onboard"):
        rid, profile, token = add_runner(conn, args.name, args.chat_id, args.max_hr, args.goal)
        if args.focus:
            conn.execute("UPDATE runners SET focus=? WHERE runner_id=?", (args.focus, rid))
            conn.commit()
        path = record.export_runner(conn, rid)
        print(f"Record file: {path}")
        print(f"runner_id: {rid}\nprofile:   {profile}\n")
        print("1) Create the Hermes profile:  hermes profile create", profile)
        print("2) In the DEFAULT profile's config.yaml, add the route under gateway.profile_routes:")
        print(f"""     gateway:
       multiplex_profiles: true
       profile_routes:
         - {{name: {profile}, platform: telegram, chat_id: "{args.chat_id}", profile: {profile}}}""")
        print(f"   Make sure {args.chat_id} is authorized (TELEGRAM_ALLOWED_USERS in the default")
        print("   profile's .env) or the DM is declined before routing. Restart the gateway.")
        print(f"3) In {profile}'s config.yaml, add the MCP server:")
        print(f"""     mcp_servers:
       coach_data:
         url: http://127.0.0.1:8100/mcp
         headers:
           Authorization: "Bearer {token}"
         identity_header: {{name: {settings.mcp_profile_header}, value_from: profile}}""")
        print("4) Set that profile's API_SERVER_KEY, then: python -m app.admin set-hermes-key "
              f"--runner {rid} --key <key>")
        print("5) Restart the gateway, then add the chat ID to TELEGRAM_ALLOWED_USERS and restart again.")
        print(f"6) python -m app.admin welcome --runner {rid}   (Sarah introduces herself and starts onboarding)")
        print("\nThe MCP token is shown once. Only its hash is stored.")
    elif args.cmd == "set-hermes-key":
        conn.execute("UPDATE runners SET hermes_api_key=? WHERE runner_id=?", (args.key, args.runner))
        conn.commit()
        print("saved")
    elif args.cmd == "coach-token":
        token = new_token()
        print(f"Coach profile MCP token: {token}\nSet in the service env: COACH_MCP_TOKEN_HASH={hash_token(token)}")
    elif args.cmd == "delete-runner":
        conn.execute("DELETE FROM runners WHERE runner_id=?", (args.runner,))
        conn.commit()
        print("Deleted runner and all their data. Also remove their Hermes profile and route.")

    elif args.cmd == "reset-runner":
        cleared = tracker_import.reset_runner(conn, args.runner)
        if args.name:
            conn.execute("UPDATE runners SET name=? WHERE runner_id=?", (args.name, args.runner))
        if args.tier:
            conn.execute("UPDATE runners SET tier=? WHERE runner_id=?", (args.tier, args.runner))
        conn.commit()
        print("Cleared:", cleared or "nothing")
        print("Profile, route, token and API key are unchanged.")
    elif args.cmd == "set-zones":
        zones = [int(x) for x in args.zones.split(",")]
        if len(zones) != 4 or zones != sorted(zones) or not 80 <= zones[0] <= zones[-1] <= 220:
            raise SystemExit("give four rising upper bounds for Z1-Z4, e.g. 132,144,152,162")
        conn.execute("UPDATE runners SET hr_zones=?, zones_locked=1 WHERE runner_id=?",
                     (json.dumps(zones), args.runner))
        if args.max_hr:
            conn.execute("UPDATE runners SET max_hr=? WHERE runner_id=?", (args.max_hr, args.runner))
        if args.easy_cap:
            conn.execute("UPDATE runners SET easy_hr_cap=? WHERE runner_id=?", (args.easy_cap, args.runner))
        conn.commit()
        print(dict(conn.execute("SELECT name, hr_zones, max_hr, easy_hr_cap, zones_locked FROM runners"
                                " WHERE runner_id=?", (args.runner,)).fetchone()))
        print("Applies to runs stored from now on. Runs already stored keep their zone times.")
    elif args.cmd == "intervals-connect":
        import os
        key = os.environ.get("INTERVALS_API_KEY", "")
        if not key:
            raise SystemExit("Set INTERVALS_API_KEY in the environment first (never on the command line).")
        intervals.connect(conn, args.runner, args.athlete_id, key)
        print(f"Runner {args.runner} linked to Intervals.icu athlete {args.athlete_id}.")
    elif args.cmd == "dex-draft":
        from datetime import date as _d

        from . import dex
        d = dex.draft(conn, args.runner, race_name=args.race, weekly_km=args.weekly_km, vdot=args.vdot,
                      long_day=args.long_day, quality_day=args.quality_day, extra_days=args.extra_days,
                      profile=args.profile, start=_d.fromisoformat(args.start) if args.start else None)
        name = conn.execute("SELECT name FROM runners WHERE runner_id=?", (args.runner,)).fetchone()[0]
        print(dex.summary_text(name, d) + "\n")
        print("\n".join(dex.week_lines(d, weeks=99)))
        if args.propose:
            p = dex.propose(conn, args.runner, d)
            hermes.notify_coach(p["text"] + f"\nReply approve {p['approval_id']} or reject {p['approval_id']}.")
            hermes.send_telegram_file(settings.coach_chat_id, p["file"], caption=f"Dex draft [{p['approval_id']}]")
            print(f"\nSent to Coach Andy as request {p['approval_id']} ({p['file']}).")
        else:
            print("\nNothing saved. Add --propose to send it to Coach Andy.")
    elif args.cmd == "dedupe-runs":
        from . import ingest
        pairs = ingest.duplicate_runs(conn, args.runner)
        for keep, extra in pairs:
            print(f"{keep['date']}  {keep['distance_km']} km {keep['duration_s']} s  keep {keep['activity_key'][:16]}"
                  f"{' (in programme)' if keep['planned'] else ''}  remove {extra['activity_key'][:16]}"
                  f" ({extra['distance_km']} km {extra['duration_s']} s)")
        if args.apply:
            print("removed:", ingest.remove_duplicate_runs(conn, args.runner))
            record.export_runner(conn, args.runner)
        else:
            print(f"{len(pairs)} duplicates found. Nothing changed; add --apply to remove them.")
    elif args.cmd == "intervals-set-coach":
        intervals.set_coach(conn, args.runner)
        print(f"Runner {args.runner}'s Intervals.icu key is now used for every athlete Coach Andy coaches.")
    elif args.cmd == "intervals-link":
        intervals.request_link(conn, args.runner, args.athlete_id)
        intervals.approve_link(conn, args.runner)
        print("Linked, waiting for access." if not onboarding.intervals_link_ready(args.runner)
              else "Linked and live: history brought in.")
    elif args.cmd == "intervals-check":
        link = intervals.link_for(conn, args.runner, any_status=True)
        print(f"athlete {link['athlete_id']}  status {link['status']}  via coach {bool(link['via_coach'])}"
              f"  last sync {link['last_sync']}")
        ok = intervals.check_access(conn, args.runner) is not None
        print("Coach Andy's key can reach this athlete." if ok else
              "No access yet: they haven't accepted the coaching request in Intervals.icu.")
        if ok and link["status"] == "approved":
            onboarding.intervals_link_ready(args.runner)
            print("Now live.")
    elif args.cmd == "intervals-push":
        res = intervals.push(conn, args.runner, days=args.days, dry_run=args.dry_run)
        if args.dry_run:
            for e in res["events"]:
                print(f"--- {e['start_date_local'][:10]}  {e['category']}  {e['name']}\n{e['description']}\n")
            print(f"{len(res['events'])} sessions would be sent.")
        else:
            print(res)
    elif args.cmd == "set-steps":
        rows_ = conn.execute("SELECT w.id, w.target, w.description FROM planned_workouts w JOIN plans p ON p.id=w.plan_id"
                             " WHERE w.runner_id=? AND w.date=? AND p.active=1", (args.runner, args.date)).fetchall()
        if len(rows_) != 1:
            raise SystemExit(f"expected one planned session on {args.date}, found {len(rows_)}")
        t = json.loads(rows_[0]["target"] or "{}")
        t["steps"] = "\n".join(x.strip() for x in args.steps.split("|") if x.strip())
        conn.execute("UPDATE planned_workouts SET target=? WHERE id=?", (json.dumps(t), rows_[0]["id"]))
        conn.commit()
        print(f"{args.date} {rows_[0]['description'][:50]}\n{t['steps']}")
        _calendar(conn, args.runner)
    elif args.cmd == "intervals-pull":
        from datetime import date as _d
        since = _d.fromisoformat(args.since)
        res = intervals.pull_activities(conn, args.runner, since)
        print("activities:", res["counts"])
        print("wellness days:", intervals.pull_wellness(conn, args.runner, since))
        record.export_runner(conn, args.runner)
    elif args.cmd == "garmin-link":
        garmin.link(conn, args.runner, args.user_id)
        print(f"Runner {args.runner} linked to Garmin user {args.user_id}.")
    elif args.cmd == "hevy-connect":
        import os
        key = os.environ.get("HEVY_API_KEY", "")
        if not key:
            raise SystemExit("Set HEVY_API_KEY in the environment first (never on the command line).")
        try:
            secret = hevy.connect(conn, args.runner, key)
        except hevy.HevyError as e:
            raise SystemExit(str(e))
        print("Hevy linked. In Hevy (hevy.com/settings?developer) > Webhooks, enter:")
        print(f"  Url: {args.public_url.rstrip('/')}/hevy/webhook")
        print(f"  Authorization header: Bearer {secret}")
        print("This secret is shown once; only its hash is stored. Then press Subscribe.")
    elif args.cmd == "hevy-import":
        link = conn.execute("SELECT r.*, h.api_key FROM hevy_links h JOIN runners r ON r.runner_id=h.runner_id"
                            " WHERE h.runner_id=?", (args.runner,)).fetchone()
        if link is None:
            raise SystemExit("run hevy-connect first")
        counts = {"stored": 0, "updated": 0, "duplicate": 0, "replaced_manual": 0}
        page = 1
        while True:
            batch = hevy.list_workouts(link["api_key"], page)
            if not batch:
                break
            older = False
            for w in batch:
                if (w.get("start_time") or "")[:10] < args.since:
                    older = True
                    continue
                res = hevy.store(conn, link, w)
                counts[res["status"]] += 1
                counts["replaced_manual"] += res.get("replaced_manual", 0)
            if older:
                break
            page += 1
        record.export_runner(conn, args.runner)
        print(counts)
    elif args.cmd == "move-session":
        from . import plans
        try:
            res = plans.move_session(conn, args.runner, args.from_date, args.to_date, note="moved by Coach Andy")
        except plans.PlanChangeError as e:
            raise SystemExit(str(e))
        print(res)
        _calendar(conn, args.runner)
        record.export_runner(conn, args.runner)
    elif args.cmd == "purge-strava":
        out = {}
        for table in ("runs", "strength_sessions"):
            out[table] = conn.execute(f"DELETE FROM {table} WHERE activity_key LIKE 'strava:%' OR source='strava'"
                                      ).rowcount
        cols = [c[1] for c in conn.execute("PRAGMA table_info(runners)") if c[1].startswith("strava")]
        for c in cols:
            conn.execute(f"UPDATE runners SET {c}=NULL")
        conn.commit()
        for (rid,) in conn.execute("SELECT runner_id FROM runners").fetchall():
            record.export_runner(conn, rid)
        print(f"Deleted Strava rows: {out}. Cleared runner columns: {cols or 'none'}.")
    elif args.cmd == "import-tracker":
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (args.runner,)).fetchone()
        if runner is None:
            raise SystemExit("no such runner")
        if tracker_import.has_data(conn, args.runner):
            if not args.replace:
                raise SystemExit("This runner already has data. Re-run with --replace to clear it first.")
            tracker_import.reset_runner(conn, args.runner)
            runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (args.runner,)).fetchone()
        print(json.dumps(tracker_import.import_workbook(conn, runner, args.file), indent=2))
        _calendar(conn, args.runner)

    elif args.cmd == "set-focus":
        onboarding.set_focus(conn, args.runner, args.focus)
        record.export_runner(conn, args.runner)
        print(f"Runner {args.runner} focus: {args.focus}")
    elif args.cmd == "buttons":
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (args.runner,)).fetchone()
        if runner is None:
            raise SystemExit("no such runner")
        keyboard.send(runner)
        print("Buttons sent:", ", ".join(b for row in keyboard.BUTTONS for b in row))
    elif args.cmd == "welcome":
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (args.runner,)).fetchone()
        if runner is None or not runner["hermes_api_key"]:
            raise SystemExit("unknown runner, or set-hermes-key has not been run yet")
        text = hermes.run_and_deliver(runner, WELCOME_PROMPT, kind="welcome")
        print("Welcome sent:\n" + text)
    elif args.cmd == "load-programme":
        print(json.dumps(programme.load(conn, args.runner, args.file, replace=args.replace), indent=2))
        _calendar(conn, args.runner)
        record.export_runner(conn, args.runner)
    elif args.cmd == "export-record":
        print(record.export_runner(conn, args.runner, args.to))
    elif args.cmd == "offboard":
        runner = conn.execute("SELECT * FROM runners WHERE runner_id=?", (args.runner,)).fetchone()
        if runner is None:
            raise SystemExit("no such runner")
        if args.keep_copy:
            print("Final copy:", record.export_runner(conn, args.runner, args.keep_copy))
        conn.execute("DELETE FROM runners WHERE runner_id=?", (args.runner,))
        conn.commit()
        record.delete_file(runner["hermes_profile"])
        print(f"Deleted {runner['name']} and everything held for them (record file included).")
        print(f"Now remove the Hermes profile {runner['hermes_profile']}, its route, and the chat ID from "
              "TELEGRAM_ALLOWED_USERS, then restart the gateway. Backups age out within 14 days.")


def _calendar(conn, rid: int) -> None:
    """After any programme change: bring the person's Intervals.icu calendar in line, if linked."""
    if not conn.execute("SELECT 1 FROM intervals_links WHERE runner_id=?", (rid,)).fetchone():
        return
    try:
        print("calendar:", intervals.reconcile(conn, rid))
    except intervals.IntervalsError as e:
        print(f"calendar NOT updated: {e} (the hourly sync will retry)")


WELCOME_PROMPT = (
    "A new person has just been set up. Send your welcome and start onboarding: call "
    "get_onboarding_status, introduce yourself as Sarah, Coach Andy's assistant, say in two or three "
    "sentences what you do, and ask for their consent as the onboarding section of your instructions "
    "describes. Ask only that one question in this message.")


if __name__ == "__main__":
    main()
