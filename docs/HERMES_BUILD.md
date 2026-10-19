# Hermes Agent build instructions (Windows)

Precise, ordered steps to set up Hermes Agent and the data service for Runner Assistant Coach
(free version) on **one Windows 10/11 PC**. Follow them top to bottom. Each step ends with a
**Check**; do not continue until it passes.

**Conventions**
- `PS>` = run in **PowerShell** (not Command Prompt), as your normal user unless it says *Admin*.
- `<ANGLE_BRACKETS>` = a value you fill in. Keep a private note of every value.
- **VERIFY** = a key or command name taken from the Hermes docs but not yet confirmed on your
  install. Confirm it with the command shown before relying on it. `hermes config set` warns
  with "did-you-mean" on unknown keys, so always read its output.
  *Nothing below needs this any more: every Hermes key and command in this guide was
  confirmed against Hermes v0.22.2 (see the Appendix).*
- Hermes keeps its files in **HERMES_HOME = `C:\Users\andre\AppData\Local\hermes`**
  (`$env:LOCALAPPDATA\hermes` in PowerShell), not `~/.hermes`. Every step below uses `$HH`:
  ```powershell
  PS> $HH = "$env:LOCALAPPDATA\hermes"
  ```
  Run that line at the start of each new PowerShell window. Key files: `$HH\config.yaml`,
  `$HH\.env` (secrets), `$HH\SOUL.md` (default persona), `$HH\logs\`.
- Named profiles live in their own folder under HERMES_HOME: `$HH\profiles\<name>`, each with
  its own `config.yaml`, `.env` and `SOUL.md`.

---

## 0. What you are building

```
Strava ──HTTPS──► Cloudflare Tunnel ──► Data service (127.0.0.1:8000)   [this repo]
                                              │  runners.db, stats
                                              │  MCP tools on 127.0.0.1:8100/mcp
Telegram ◄──► Hermes gateway (multiplexed profiles) ◄──┘
                ├─ coach        ← your chat (alerts, approvals)
                └─ runner-<N>   ← one per runner (MCP tools only)

Unauthorized / unrouted chats never reach a profile: the gateway answers them with the
decline message (section 8). There is no "fallback" profile — see §9.1.
```

### Windows-specific facts to accept up front
1. **The PC must stay on and awake.** If it sleeps, runners get no debriefs and Strava events
   are missed. Section 2 sets power options.
2. **Strava needs a public HTTPS address** to send new-activity events. A home PC does not have
   one, so a free Cloudflare Tunnel provides it (section 5). Hermes itself needs no public
   address: Telegram is reached outbound.
3. Background services use **NSSM** (runs programs as Windows services) and **Task Scheduler**
   (replaces cron).

---

## 1. Prerequisites (collect before starting)

| Item | Where | Value name |
| --- | --- | --- |
| Windows 10/11 PC that can stay on | — | — |
| Telegram bot token | @BotFather → `/newbot` | `<TELEGRAM_BOT_TOKEN>` |
| Your Telegram chat ID | Message @userinfobot | `<COACH_CHAT_ID>` |
| Anthropic API key | console.anthropic.com | `<ANTHROPIC_API_KEY>` |
| Strava API app (needs a Strava subscription) | strava.com/settings/api | `<STRAVA_CLIENT_ID>`, `<STRAVA_CLIENT_SECRET>` |
| A domain on Cloudflare (free plan is fine) | dash.cloudflare.com | `<DOMAIN>`, e.g. `coach.example.com` |

**Check:** you have every value written down.

---

## 2. Prepare Windows

*Admin* PowerShell (right-click Start → Terminal (Admin)):

```powershell
PS> powercfg /change standby-timeout-ac 0
PS> powercfg /change hibernate-timeout-ac 0
PS> winget install --id Python.Python.3.12 -e
PS> winget install --id Git.Git -e
PS> winget install --id NSSM.NSSM -e
PS> winget install --id Cloudflare.cloudflared -e
```

Close and reopen PowerShell (normal user) so the new programs are on PATH.

**Check:**
```powershell
PS> python --version        # 3.12.x
PS> git --version
PS> nssm version
PS> cloudflared --version
```

---

## 3. Install the data service

```powershell
PS> cd $HOME
PS> git clone https://github.com/sizzleguyandy/sizweumoya.git
PS> cd sizweumoya
PS> python -m venv .venv
PS> .\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
PS> pip install -r requirements.txt
PS> python -m pytest -q               # expect: 10 passed
PS> Copy-Item .env.example .env
PS> notepad .env
```

Fill in `.env`:

| Key | Value |
| --- | --- |
| `RAC_DB_PATH` | `C:\Users\<YOU>\sizweumoya\data\runners.db` |
| `PUBLIC_BASE_URL` | `https://<DOMAIN>` |
| `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` | from step 1 |
| `STRAVA_VERIFY_TOKEN` | any random string (`[guid]::NewGuid()`) |
| `HERMES_BASE_URL` | `http://127.0.0.1:8642` |
| `TELEGRAM_BOT_TOKEN` | from step 1 |
| `COACH_CHAT_ID` | from step 1 |
| `JOBS_TOKEN` | any random string (`[guid]::NewGuid()`) |
| `COACH_MCP_TOKEN_HASH` | leave empty for now (step 9) |

```powershell
PS> python -m app.admin init
```

**Check:** `data\runners.db` now exists.

---

## 4. Run the data service as two Windows services

The app reads settings from environment variables; NSSM passes them from `.env`.
*Admin* PowerShell, in `C:\Users\<YOU>\sizweumoya`:

```powershell
PS> $dir = "$HOME\sizweumoya"
PS> $py  = "$dir\.venv\Scripts\python.exe"
PS> $envs = (Get-Content "$dir\.env" | Where-Object { $_ -match '^\s*[A-Z_]+=' }) -join "`n"

PS> nssm install rac-web $py "-m uvicorn app.main:app --host 127.0.0.1 --port 8000"
PS> nssm set rac-web AppDirectory $dir
PS> nssm set rac-web AppEnvironmentExtra $envs
PS> nssm set rac-web AppStdout "$dir\data\web.log"
PS> nssm set rac-web AppStderr "$dir\data\web.log"
PS> nssm start rac-web

PS> nssm install rac-mcp $py "-m app.mcp_server"
PS> nssm set rac-mcp AppDirectory $dir
PS> nssm set rac-mcp AppEnvironmentExtra $envs
PS> nssm set rac-mcp AppStdout "$dir\data\mcp.log"
PS> nssm set rac-mcp AppStderr "$dir\data\mcp.log"
PS> nssm start rac-mcp
```

Whenever you edit `.env`, rerun the three `$envs` / `AppEnvironmentExtra` lines and
`nssm restart rac-web` / `nssm restart rac-mcp`.

**Check:**
```powershell
PS> Invoke-RestMethod http://127.0.0.1:8000/health     # ok : True
PS> Get-Service rac-web, rac-mcp                       # both Running
```

---

## 5. Public HTTPS for Strava (Cloudflare Tunnel)

```powershell
PS> cloudflared tunnel login                 # opens browser; choose your domain
PS> cloudflared tunnel create rac
PS> cloudflared tunnel route dns rac <DOMAIN>
```

Create `$HOME\.cloudflared\config.yml` (use the tunnel ID printed by `create`):
```yaml
tunnel: <TUNNEL_ID>
credentials-file: C:\Users\<YOU>\.cloudflared\<TUNNEL_ID>.json
ingress:
  - hostname: <DOMAIN>
    path: ^/(strava|health)
    service: http://127.0.0.1:8000
  - service: http_status:404
```
Only `/strava/*` and `/health` are public. `/jobs`, the MCP server and Hermes stay private.

*Admin* PowerShell:
```powershell
PS> cloudflared service install
PS> Start-Service cloudflared
```

**Check:** from your phone (mobile data, not Wi-Fi) open `https://<DOMAIN>/health` → `{"ok":true}`.

---

## 6. Register the Strava webhook (once)

```powershell
PS> Invoke-RestMethod -Method Post https://www.strava.com/api/v3/push_subscriptions -Body @{
      client_id = "<STRAVA_CLIENT_ID>"; client_secret = "<STRAVA_CLIENT_SECRET>"
      callback_url = "https://<DOMAIN>/strava/webhook"; verify_token = "<STRAVA_VERIFY_TOKEN>" }
```
In the Strava API settings page set **Authorization Callback Domain** to `<DOMAIN>`.

**Check:** the command returns an `id`. (Strava calls your `/strava/webhook` to verify it first;
if it fails, check `data\web.log`.)

---

## 7. Install Hermes and set the model

Install Hermes with the official Windows instructions
(https://hermes-agent.nousresearch.com/docs), as your normal user. Then:

```powershell
PS> hermes --version
PS> hermes config set ANTHROPIC_API_KEY <ANTHROPIC_API_KEY>
PS> hermes model                                   # choose Anthropic and a Claude model
PS> hermes config set timezone Africa/Johannesburg # your IANA zone
PS> hermes doctor
```

**Check:** `hermes chat -q "Say OK"` replies `OK`, and `hermes doctor` shows no errors.

---

## 8. Global safety settings (default profile)

```powershell
PS> hermes config set unauthorized_dm_behavior decline
PS> hermes config set unauthorized_dm_decline_message "This coaching bot is for registered runners. Ask your coach for access."
PS> hermes config set group_sessions_per_user true
PS> hermes config set privacy.redact_pii true
PS> hermes config set approvals.mode manual
PS> hermes config set terminal.backend local
```

**Who may talk to the bot.** The gateway only serves chats that are *authorized*, and an
unknown DM is handled by `unauthorized_dm_behavior` (`decline` = reply once with the message
above and do nothing else). Authorize the coach and every runner by listing their Telegram
user IDs — in a DM the chat ID **is** the user ID:

```powershell
PS> hermes config set TELEGRAM_ALLOWED_USERS "<COACH_CHAT_ID>,<RUNNER_CHAT_ID>,<RUNNER2_CHAT_ID>"
```

`TELEGRAM_ALLOWED_USERS` is platform-wide, so one list on the shared bot covers every runner
profile. Re-run the command with the full list each time you add a runner.

*Alternative:* leave `unauthorized_dm_behavior` at its default (`pair`) and approve the
code the bot DMs a new runner with `hermes pairing approve telegram <request-id>`. Pairing
whitelists are **per profile**, so a runner routed to `runner-<N>` must be paired in that
profile's own store — the allowlist above is the simpler, predictable path.

**Check:** `hermes config` lists each value; no did-you-mean warnings. Messaging the bot from an
account that is on neither list returns the decline text and nothing else.

---

## 9. Create the coach profile

```powershell
PS> hermes profile create coach
PS> hermes profile list                # shows default, coach
```

Named profiles live in `$HH\profiles\<name>` and keep their own `config.yaml`, `.env` and
`SOUL.md`.

### 9.1 Why there is no "fallback" profile
Hermes has no per-chat "unknown profile" setting: a message that matches no
`gateway.profile_routes` entry is handled by the **default** profile (Hermes v0.22.2:
`gateway/run.py` `_profile_name_for_source` falls back to the active profile; there is no
`gateway.default_profile` key). Unknown chats never reach a runner profile, and an
unauthorized DM is answered by `unauthorized_dm_behavior` (section 8) before any tool runs.
That is the whole "sign-up" story — one decline message, no extra profile to maintain.
Do **not** create a `fallback` profile: nothing can route to it.

### 9.2 Coach profile
```powershell
PS> cd $HOME\sizweumoya; .\.venv\Scripts\Activate.ps1
PS> python -m app.admin coach-token
```
Put the printed `COACH_MCP_TOKEN_HASH=...` in `.env`, refresh the NSSM environment and restart
`rac-mcp` (see end of section 4). Keep `<COACH_TOKEN>`.

`hermes -p coach config edit`, add:
```yaml
mcp_servers:
  coach_data:
    url: http://127.0.0.1:8100/mcp
    headers:
      Authorization: "Bearer <COACH_TOKEN>"
    identity_header: {name: x-hermes-profile, value_from: profile}
agent:
  disabled_toolsets: [terminal, web, browser]
```
`identity_header` **must be a mapping** `{name, value_from}` — `value_from: profile` makes
Hermes send the active profile name (`coach`) in that header. A bare string is ignored with a
warning and *no header is sent* (Hermes v0.22.2: `tools/mcp_tool_errors.py`
`_resolve_identity_header`). The `name` must equal `MCP_PROFILE_HEADER` in the data service
`.env` (default `x-hermes-profile`). It only works on HTTP servers, which this is.

Coach persona, `notepad "$HH\profiles\coach\SOUL.md"`:
```markdown
You are the head coach's admin assistant. You only talk to the head coach.
Use coach_pending_approvals, coach_decide and coach_open_alerts.
When the coach says "approve <id>", "reject <id>" or "edit <id>: <note>", call coach_decide
with that exact id. Never invent an id. Confirm what was recorded in one line.
```
**Check:** `hermes -p coach chat -q "list pending approvals"` returns an empty list, not a
permission error.

---

## 10. Your own runner profile (template for every runner)

You need a **second Telegram account** for this (a chat ID can map to only one profile, and
your main account is the coach).

### 10.1 Register in the data service
```powershell
PS> python -m app.admin add-runner --name "<NAME>" --chat-id <RUNNER_CHAT_ID> --max-hr <MAX_HR> --goal "<GOAL>"
```
Note `runner_id` `<N>`, profile `runner-<N>` and the one-time `<RUNNER_TOKEN>`.

### 10.2 Create and configure the profile
```powershell
PS> hermes profile create runner-<N>
PS> hermes -p runner-<N> config edit
```
```yaml
mcp_servers:
  coach_data:
    url: http://127.0.0.1:8100/mcp
    headers:
      Authorization: "Bearer <RUNNER_TOKEN>"
    identity_header: {name: x-hermes-profile, value_from: profile}
agent:
  disabled_toolsets: [terminal, web, browser, memory]
memory:
  write_approval: true
skills:
  write_approval: true
privacy:
  redact_pii: true
```
`value_from: profile` sends `runner-<N>` — exactly the name stored in `runners.hermes_profile`,
so the data service can resolve who is calling. The bearer token is that runner's one-time
secret; together they are the only identity the MCP tools use (no tool accepts a runner id).

### 10.3 Persona
```powershell
PS> Copy-Item "$HOME\sizweumoya\hermes\SOUL.md" "$HH\profiles\runner-<N>\SOUL.md"
```

### 10.4 API key for the data service
```powershell
PS> $key = -join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_})
PS> hermes -p runner-<N> config set API_SERVER_KEY $key
PS> python -m app.admin set-hermes-key --runner <N> --key $key
```

**Check:** `hermes -p runner-<N> chat -q "what is my goal?"` answers with your goal (it called
`get_my_profile`).

---

## 11. Routing and API server (default profile)

`hermes config edit` (default profile), add:
```yaml
gateway:
  multiplex_profiles: true
  profile_routes:
    - name: coach
      platform: telegram
      chat_id: "<COACH_CHAT_ID>"
      profile: coach
    - name: runner-<N>
      platform: telegram
      chat_id: "<RUNNER_CHAT_ID>"
      profile: runner-<N>
```
`profile_routes` is a **list of entries**, each with `name`, `platform`, `chat_id`, `profile`
(Hermes v0.22.2: `gateway/profile_routing.py` `parse_profile_routes`; `gateway/config.py:643`
declares it as a list). There is **no** `gateway.default_profile` / `fallback` key — see §9.1.
Quote the chat IDs: an unquoted number is loaded as an int and still coerced, but quoting
avoids the load-time warning. A route whose `profile` does not exist, or is not served by the
running gateway, is refused (the message is logged and dropped), so create the profile first.

Docs: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/multi-profile-gateways.md

API server — the data service starts runner turns on `POST /p/<profile>/v1/runs`:
```powershell
PS> hermes config set API_SERVER_ENABLED true
PS> hermes config set API_SERVER_HOST 127.0.0.1
PS> hermes config set API_SERVER_PORT 8642
PS> hermes config check
```
Those three go in the **default** profile's `.env` (they are UPPER_SNAKE, so `hermes config
set` routes them there). The single listener serves every profile through its `/p/<profile>/`
prefix; each profile needs **its own** `API_SERVER_KEY`, set in that profile's `.env` (step
10.4). A `/p/<profile>/` request authenticated with the default profile's key is rejected
with 401, which is what keeps runners apart.

**Check:** `hermes config check` reports no unknown keys under `gateway`.

---

## 12. Start the gateway and keep it running

```powershell
PS> hermes gateway setup          # choose Telegram, paste <TELEGRAM_BOT_TOKEN>
PS> hermes gateway                # run in the foreground once; watch for "Telegram connected"
```
Stop it with Ctrl+C, then install it as a service. Hermes can do this itself on Windows —
`hermes gateway install` registers a **Windows Scheduled Task**:
```powershell
PS> hermes gateway install
PS> hermes gateway status
```
NSSM is the alternative if you prefer a real Windows service (*Admin* PowerShell):
```powershell
PS> $hermes = (Get-Command hermes).Source
PS> nssm install hermes-gateway $hermes "gateway"
PS> nssm set hermes-gateway AppDirectory $HOME
PS> nssm set hermes-gateway ObjectName ".\<WINDOWS_USERNAME>" "<WINDOWS_PASSWORD>"
PS> nssm start hermes-gateway
```
`ObjectName` makes the service run as **you**. Also pin HERMES_HOME so the service can never
fall back to a different folder:
```powershell
PS> nssm set hermes-gateway AppEnvironmentExtra "HERMES_HOME=$env:LOCALAPPDATA\hermes"
PS> nssm restart hermes-gateway
```
(If your Windows sign-in is a Microsoft account with no password/PIN-only, use Task Scheduler
instead: trigger *At log on*, action `hermes gateway`, "Run whether user is logged on or not".)

**Check:** `hermes status --full` shows Telegram connected; `Get-Service hermes-gateway` is Running.

Then test the API server. The run API is **asynchronous**: the POST only admits the turn and
returns a `run_id`; the reply comes from polling the run.
```powershell
PS> $h = @{Authorization = "Bearer $key"; "Content-Type" = "application/json"}
PS> $r = Invoke-RestMethod -Method Post http://127.0.0.1:8642/p/runner-<N>/v1/runs `
      -Headers $h -Body '{"input":"Say hello in five words."}'
PS> $r | ConvertTo-Json -Depth 10      # {"run_id":"run_...","status":"started","replayed":false}
PS> $id = $r.run_id
PS> do { Start-Sleep -Seconds 2
         $s = Invoke-RestMethod -Headers $h "http://127.0.0.1:8642/p/runner-<N>/v1/runs/$id" }
    until ($s.status -in @("completed","failed","cancelled","interrupted"))
PS> $s | ConvertTo-Json -Depth 10      # status, output (the final text), usage, session_id
```
`output` holds the assistant's final reply and is only reliable once `status` is `completed`.
`app/hermes.py` already does exactly this (`create_run` → `wait_for_reply`) and delivers
`output` to Telegram; a non-`completed` terminal status raises `RunFailed`, which the callers
in `app/main.py` log. If the POST returns 401, `$key` is not that profile's `API_SERVER_KEY`
(step 10.4) — the default profile's key is refused on a `/p/<profile>/` URL.

---

## 13. Scheduled jobs (Task Scheduler)

```powershell
PS> $tok = (Select-String -Path "$HOME\sizweumoya\.env" -Pattern '^JOBS_TOKEN=(.+)$').Matches[0].Groups[1].Value
PS> function New-Job($name, $path, $trigger) {
      $cmd = "Invoke-RestMethod -Method Post -Headers @{Authorization='Bearer $tok'} http://127.0.0.1:8000/jobs/$path"
      $act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -Command `"$cmd`""
      Register-ScheduledTask -TaskName $name -Action $act -Trigger $trigger -Force }
PS> New-Job "RAC weekly"  "weekly"        (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 18:00)
PS> New-Job "RAC quiet"   "quiet-runners" (New-ScheduledTaskTrigger -Daily -At 08:00)
PS> New-Job "RAC fitness" "fitness"       (New-ScheduledTaskTrigger -Weekly -WeeksInterval 4 -DaysOfWeek Monday -At 07:00)
```
Nightly backup (the database is a single file):
```powershell
PS> $b = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -Command `"Copy-Item $HOME\sizweumoya\data\runners.db $HOME\OneDrive\rac-backup\runners-`$(Get-Date -f yyyyMMdd).db`""
PS> New-Item -ItemType Directory -Force "$HOME\OneDrive\rac-backup" | Out-Null
PS> Register-ScheduledTask -TaskName "RAC backup" -Action $b -Trigger (New-ScheduledTaskTrigger -Daily -At 03:00) -Force
```

**Check:** in Task Scheduler, right-click **RAC weekly → Run**; the weekly summary arrives in
your runner chat.

---

## 14. End-to-end checks (in order)

| # | Do this | Expected |
| --- | --- | --- |
| 1 | From an unauthorized Telegram account, message the bot | The decline text; nothing else |
| 2 | Runner chat: "what's my goal?" | Your goal |
| 3 | Runner chat: "show Sarah's runs" | Refuses; it only sees your data |
| 4 | Coach chat: "list pending approvals" | Empty list |
| 5 | Open `https://<DOMAIN>/strava/connect/<N>` → follow the `url` → approve | "Strava connected" |
| 6 | Do a real run with HR | Debrief in the runner chat within minutes |
| 7 | Log a Hevy workout | Strength debrief lists your lifts |
| 8 | Runner chat: "my calf is sore" | Kind reply + alert in coach chat |
| 9 | Runner chat: "move tomorrow's run to Friday" | "Sent to your coach" + request in coach chat |
| 10 | Coach chat: "approve <id>" | Runner chat gets the decision |

Send any failure (exact message, plus `data\web.log` / `data\mcp.log`) to Claude.

---

## 15. Add a runner (repeat per runner)

1. `python -m app.admin add-runner ...` (10.1)
2. `hermes profile create runner-<N>`, paste config with the new token (10.2), copy SOUL.md (10.3)
3. Set and store the API key (10.4)
4. Add the route entry for `"<CHAT_ID>"` → `runner-<N>` under `gateway.profile_routes` (11) and
   add their chat ID to `TELEGRAM_ALLOWED_USERS` (8); then `nssm restart hermes-gateway`
5. Send them `https://<DOMAIN>/strava/connect/<N>` (it returns the Strava link)
6. Ask them to message the bot "hi"; confirm they get a normal reply, not the decline text

## 16. Remove a runner

1. `python -m app.admin delete-runner --runner <N>` (deletes all their data)
2. Remove their entry from `gateway.profile_routes` and their ID from `TELEGRAM_ALLOWED_USERS`;
   `nssm restart hermes-gateway`
3. `hermes profile delete runner-<N>`

---

## Appendix: what was verified on Hermes v0.22.2

Every key and command below was checked against the installed source, not just the docs.

| Question | Answer | Where it was confirmed |
| --- | --- | --- |
| `gateway.profile_routes` shape | A **list** of `{name, platform, chat_id, profile}` entries | `gateway/profile_routing.py` `parse_profile_routes`; `gateway/config.py:643` |
| Unknown-chat / fallback profile | No such key. An unrouted message is served by the **default** profile; unauthorized DMs get `unauthorized_dm_behavior` | `gateway/run.py` `_profile_name_for_source` (~line 4429/4450); `hermes config get gateway.default_profile` → "Config key not set" |
| Unauthorized DMs | `unauthorized_dm_behavior` = `pair` (default) / `ignore` / `decline`; `decline` uses `unauthorized_dm_decline_message`. Access via `TELEGRAM_ALLOWED_USERS` (platform-wide) or a per-profile pairing approval | `gateway/authz_mixin.py:698`; `gateway/pairing.py:68`; `gateway/run.py` `_init_registries_and_clocks` (per-profile pairing stores) |
| MCP server keys | `mcp_servers.<name>` with `url` (HTTP transport), `headers`, and `identity_header` | `tools/mcp_tool_config.py` `_load_mcp_config`; `tools/mcp_tool_transport.py:145` |
| `identity_header` value | A **mapping** `{name, value_from: static\|profile, value}`; `profile` sends the active profile name. A string is ignored with a warning | `tools/mcp_tool_errors.py` `_resolve_identity_header`, applied at `tools/mcp_tool_transport.py:148`; HTTP-only (`:356`) |
| API-server enable keys | `API_SERVER_ENABLED`, `API_SERVER_HOST`, `API_SERVER_PORT`, `API_SERVER_KEY` (required per profile) | `gateway/config_env.py:314`; docs `/user-guide/features/api-server` |
| Starting a run | `POST /p/<profile>/v1/runs` body `{"input": "..."}` → **202** `{"run_id", "status": "started", "replayed"}` | `gateway/platforms/api_server_runs.py` `_handle_runs`, `_accepted_response` |
| Getting the reply | `GET /p/<profile>/v1/runs/<run_id>` while `status` is `started`/`running`; the text is in `output` once `status` is `completed` (terminal: `completed`/`failed`/`cancelled`/`interrupted`) | `api_server_runs.py` `_handle_get_run`, `_set_run_status`; `output=result.get("final_response")` at `:986` |
| Profile commands | `hermes profile create` / `list` / `delete` all exist; profiles live in `$HH\profiles\<name>` | `hermes profile --help`; `hermes_cli/profiles.py:432` |
| Gateway service on Windows | `hermes gateway install` exists (creates a Windows Scheduled Task); NSSM is only an alternative | `hermes gateway --help`; `hermes gateway status` output |

