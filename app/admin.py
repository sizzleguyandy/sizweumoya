"""Admin CLI.

  python -m app.admin init
  python -m app.admin add-runner --name Andrew --chat-id 123456789 --max-hr 188 --goal "Sub-2 half"
  python -m app.admin set-hermes-key --runner 1 --key <API_SERVER_KEY of that profile>
  python -m app.admin coach-token
  python -m app.admin delete-runner --runner 1
"""
import argparse
import json

from . import stats
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
    args = p.parse_args()

    conn = connect()
    init_db(conn)
    if args.cmd == "init":
        print(f"Initialised {settings.db_path}")
    elif args.cmd == "add-runner":
        rid, profile, token = add_runner(conn, args.name, args.chat_id, args.max_hr, args.goal)
        print(f"runner_id: {rid}\nprofile:   {profile}\n")
        print("1) Create the Hermes profile:  hermes profile create", profile)
        print("2) Add to the gateway config, under gateway.profile_routes:")
        print(f"     telegram:{args.chat_id} -> {profile}   (check exact syntax in the multi-profile docs)")
        print(f"3) In {profile}'s config.yaml, add the MCP server:")
        print(f"""     mcp_servers:
       coach_data:
         url: http://127.0.0.1:8100/mcp
         headers:
           Authorization: "Bearer {token}"
         identity_header: {settings.mcp_profile_header}   # sends the profile name (check key name)""")
        print("4) Set that profile's API_SERVER_KEY, then: python -m app.admin set-hermes-key "
              f"--runner {rid} --key <key>")
        print(f"5) Send the runner their Strava link: {settings.public_base_url}/strava/connect/{rid}")
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


if __name__ == "__main__":
    main()
