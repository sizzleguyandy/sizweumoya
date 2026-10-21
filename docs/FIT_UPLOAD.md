# File uploads (replaces Strava)

Runners send their activities to the bot themselves. Nothing comes from Strava any more.

| Runner sends | Sarah does |
| --- | --- |
| A `.fit` file (run or watch strength session), optional caption | `submit_activity_file(file_path, note=caption)` → debrief (no debrief for runs under 3 km and under 20 min) |
| A `.zip` (Garmin Connect "Export Original", or many `.fit` files) | Same tool; a history import → `get_baseline` → baseline snapshot |
| A typed gym session, e.g. "Squat 3x8 60kg; RDL 3x10 50kg" | `log_workout(text)` |

## How a file gets from Telegram to the data service

1. The runner sends the file to `@SizweUmoya_bot`.
2. Hermes saves the attachment on the PC and tells the runner's agent the local path.
3. The agent calls `submit_activity_file` with that path.
4. The data service accepts the path **only** if it resolves inside
   `UPLOAD_ROOT` for the authenticated profile (default `{hermes_home}/profiles/{profile}`).
   The profile comes from the identity header + token, never from the model, so a runner can
   never submit another runner's file or any other file on the PC.
5. Each file is identified by its SHA-256: re-sending is harmless.

Limits: `.fit` or `.zip` only; 25 MB per file; up to 300 activities per zip; zips are read in
memory, never unpacked to disk.

## Setup on the PC (Hermes agent)

1. **Pull this branch** and `pip install -r requirements.txt` (adds `fitdecode`).
2. **Check where Hermes saves Telegram attachments** for a routed profile, and whether the agent
   is given the local path. Send a test `.fit` from the runner account and look for it.
   - If it lands under `%LOCALAPPDATA%\hermes\profiles\runner-1\...`: the default works.
   - If it lands in a shared folder (e.g. `%LOCALAPPDATA%\hermes\cache\...`) set `UPLOAD_ROOT`
     to that folder with `{profile}` in it if Hermes separates per profile. **If Hermes does not
     separate attachments per profile, stop and report** — a shared folder would let one
     runner's agent submit another runner's file, so we need another approach.
   - Check that `.fit` documents are actually delivered to the agent (some gateways only pass
     images/audio). Telegram bots can download files up to 20 MB.
3. In `.env`: set `HERMES_HOME=C:\Users\andre\AppData\Local\hermes` and, if needed, `UPLOAD_ROOT`.
   Remove the `STRAVA_*` and `PUBLIC_BASE_URL` lines (no longer used). Keep LF line endings.
4. Restart the web service and MCP server. On start the database migrates itself
   (`strava_id` → `activity_key`; existing rows become `strava:<id>`).
5. Copy `hermes/SOUL.md` into each runner profile.
6. **Strava clean-up** (data no longer used):
   - delete webhook subscription 378009;
   - clear runner-1's Strava tokens and any `strava:` runs;
   - in the Cloudflare tunnel config, remove the `/strava` route (keep `/health`, or stop the
     tunnel entirely, since nothing needs to reach the PC from outside any more).

## Checks

| # | Do this (runner account) | Expected |
| --- | --- | --- |
| 1 | Send a `.fit` run with caption "hot today" | Debrief mentioning the heat |
| 2 | Send the same file again | "I already have that one" |
| 3 | Send a `.zip` of several runs | Thanks + baseline snapshot |
| 4 | Type "Squat 3x8 60kg; RDL 3x10 50kg" | Strength session acknowledged |
| 5 | Send a `.txt` or photo | Kind "please send the .fit file" |
| 6 | Ask Sarah to submit a file path under another runner's folder | Refused |

## Getting a .fit file

- **Garmin:** Garmin Connect website (works in a phone browser) → activity → ⚙ → *Export Original*
  → send the `.zip` to the bot.
- **Coros, Suunto, Polar:** in the phone app, open the activity → share/export → `.fit` → Telegram.
