"""Admin CLI.

  python -m app.admin init
  python -m app.admin add-runner --name Andrew --chat-id 123456789 --max-hr 188 --goal "Sub-2 half"
  python -m app.admin set-hermes-key --runner 1 --key <API_SERVER_KEY of that profile>
  python -m app.admin coach-token
  python -m app.admin delete-runner --runner 1
  python -m app.admin reset-runner --runner 1 --name Andy --tier full
  python -m app.admin set-zones --runner 1 --zones 132,144,152,162 [--max-hr 184] [--easy-cap 138]
  python -m app.admin import-tracker --runner 1 --file tracker.xlsx [--replace]
"""
import argparse
import json

from . import stats, tracker_import
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
    a = sub.add_parser("add-runner")
    a.add_argument("--name", required=True)
    a.add_argument("--chat-id", required=True)
    a.add_argument("--max-hr", type=int)
    a.add_argument("--goal")
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
    t = sub.add_parser("import-tracker")
    t.add_argument("--runner", type=int, required=True)
    t.add_argument("--file", required=True)
    t.add_argument("--replace", action="store_true", help="clear the runner's existing data first")
    args = p.parse_args()

    conn = connect()
    init_db(conn)
    if args.cmd == "init":
        print(f"Initialised {settings.db_path}")
    elif args.cmd == "add-runner":
        rid, profile, token = add_runner(conn, args.name, args.chat_id, args.max_hr, args.goal)
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
        print("5) Ask the runner to send their last 6-8 weeks of runs as .fit files (or a zip) to the bot.")
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


if __name__ == "__main__":
    main()
