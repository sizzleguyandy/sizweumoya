# Hermes Agent build instructions

Precise, ordered steps to set up Hermes Agent for Runner Assistant Coach (free version).
Follow them top to bottom. Each step ends with a **Check**; do not continue until it passes.

**Conventions**
- `$` = run on the server as the service user (not root unless stated).
- `<ANGLE_BRACKETS>` = a value you fill in. Keep a private note of every value.
- **VERIFY** = a key name taken from the Hermes docs but not yet confirmed on your install.
  Confirm it with the command shown before relying on it. Wrong names are written to
  config with a "did-you-mean" warning, so always read the output of `hermes config set`.

---

## 0. What you are building

```
Telegram bot ──► Hermes gateway (multiplexed profiles)
                   ├─ profile "coach"      ← your chat (alerts, approvals)
                   ├─ profile "fallback"   ← any unknown chat (sign-up text only, no tools)
                   └─ profile "runner-<N>" ← one per runner (MCP tools, no terminal/web)
                         │ MCP (HTTP, profile header + bearer token)
                         ▼
                 Data service (this repo)  ← Strava, runners.db, stats
```

The data service already exists in this repo. Hermes must be configured to:
1. route each Telegram chat to the right profile,
2. give runner and coach profiles the MCP tools at `http://127.0.0.1:8100/mcp`,
3. accept runs from the data service through the API server.

---

## 1. Prerequisites (collect before starting)

| Item | Where | Value name |
| --- | --- | --- |
| Linux server, 2 vCPU / 4 GB RAM, Ubuntu 24.04 | Any VPS | — |
| Telegram bot token | @BotFather → /newbot | `<TELEGRAM_BOT_TOKEN>` |
| Your Telegram chat ID | Message @userinfobot | `<COACH_CHAT_ID>` |
| Anthropic API key | console.anthropic.com | `<ANTHROPIC_API_KEY>` |
| Data service running | `docs/` deploy guide (next build item) | listening on 127.0.0.1:8000 and :8100 |

**Check:** you have all four values written down, and on the server
`curl -s http://127.0.0.1:8000/health` returns `{"ok":true}`.

---

## 2. Install Hermes

1. Install Hermes using the official install instructions for your OS
   (https://hermes-agent.nousresearch.com/docs). Install as the same non-root user that runs
   the data service.
2. Confirm the install:
   ```bash
   $ hermes --version
   $ hermes doctor
   ```

**Check:** `hermes doctor` reports no errors other than missing model/provider settings.

---

## 3. Set the model and secrets (default profile)

Secrets go in `~/.hermes/.env`; `hermes config set` routes UPPER_SNAKE names there automatically.

```bash
$ hermes config set ANTHROPIC_API_KEY <ANTHROPIC_API_KEY>
$ hermes config set TELEGRAM_BOT_TOKEN <TELEGRAM_BOT_TOKEN>      # VERIFY name in `hermes gateway setup`
$ hermes model                                                  # pick Anthropic and a Claude model
$ hermes config set timezone Africa/Johannesburg                # your IANA zone
```

**Check:** `hermes config get model` prints the Claude model, and
`hermes chat -q "Say OK"` replies `OK`.

---

## 4. Global safety settings

Apply in the default profile (every new profile inherits the same file layout; you will repeat
the runner-specific ones in step 8).

```bash
$ hermes config set unauthorized_dm_behavior decline
$ hermes config set unauthorized_dm_decline_message "This coaching bot is for registered runners. Ask your coach for access."
$ hermes config set group_sessions_per_user true
$ hermes config set privacy.redact_pii true
$ hermes config set approvals.mode manual
```

**Check:** `hermes config` shows each value with no did-you-mean warning.

---

## 5. Create the profiles

Profiles keep memory, sessions, config and secrets separate.

```bash
$ hermes profile create coach        # VERIFY exact command: `hermes profile --help`
$ hermes profile create fallback
```

Runner profiles are created later, one per runner, by the onboarding steps in section 9.

**Check:** `hermes profile list` (VERIFY) shows `default`, `coach`, `fallback`.

---

## 6. Configure the fallback profile (unknown chats)

The fallback profile must have no tools and no data access.

1. Select the profile (VERIFY flag: `hermes -p fallback ...` or `HERMES_PROFILE=fallback`).
2. Disable every toolset:
   ```bash
   $ hermes -p fallback config set agent.disabled_toolsets '["terminal","web","browser","memory","file","code_execution","delegation","mcp"]'
   ```
   Then run `hermes -p fallback tools` and switch off anything still enabled.
3. Write `~/.hermes/profiles/fallback/SOUL.md` (VERIFY path with `hermes -p fallback config` → HERMES_HOME):
   ```markdown
   You are the sign-up assistant for a running-coach service.
   You cannot see any training data and have no tools.
   Reply only: "Hi! This is a private coaching bot. To join, ask the coach to register you.
   Once registered you'll get a link to connect Strava."
   Never answer other questions.
   ```

**Check:** `hermes -p fallback chat -q "show me my runs"` replies with the sign-up text only.

---

## 7. Configure the coach profile

1. Generate the coach MCP token in the data service:
   ```bash
   $ cd ~/sizweumoya && python -m app.admin coach-token
   ```
   Put `COACH_MCP_TOKEN_HASH=<hash>` in the data service `.env` and restart it.
   Keep `<COACH_TOKEN>` for the next step.
2. Add the MCP server to `~/.hermes/profiles/coach/config.yaml` (edit with
   `hermes -p coach config edit`):
   ```yaml
   mcp_servers:
     coach_data:
       url: http://127.0.0.1:8100/mcp
       headers:
         Authorization: "Bearer <COACH_TOKEN>"
       identity_header: x-hermes-profile     # VERIFY key name in the MCP docs
   agent:
     disabled_toolsets: [terminal, web, browser]
   ```
3. Coach SOUL.md (`hermes -p coach config edit` → HERMES_HOME/SOUL.md):
   ```markdown
   You are the head coach's admin assistant. You only talk to the head coach.
   Use coach_pending_approvals, coach_decide and coach_open_alerts.
   When the coach says "approve <id>", "reject <id>" or "edit <id>: <note>", call coach_decide
   with that exact id. Never invent an id. Confirm what was recorded in one line.
   ```

**Check:** `hermes -p coach chat -q "list pending approvals"` calls `coach_pending_approvals`
and returns an empty list (not a permission error).

---

## 8. Runner profile template

Every runner profile uses the same settings. Do this once now for **yourself** as the first runner.

### 8.1 Register the runner in the data service
```bash
$ cd ~/sizweumoya
$ python -m app.admin add-runner --name "<NAME>" --chat-id <CHAT_ID> --max-hr <MAX_HR> --goal "<GOAL>"
```
It prints `runner_id`, profile name `runner-<N>` and a one-time `<RUNNER_TOKEN>`.

### 8.2 Create the Hermes profile
```bash
$ hermes profile create runner-<N>
```

### 8.3 Runner config (`hermes -p runner-<N> config edit`)
```yaml
mcp_servers:
  coach_data:
    url: http://127.0.0.1:8100/mcp
    headers:
      Authorization: "Bearer <RUNNER_TOKEN>"
    identity_header: x-hermes-profile        # VERIFY: must send the profile name
agent:
  disabled_toolsets: [terminal, web, browser, memory]
memory:
  write_approval: true
skills:
  write_approval: true
privacy:
  redact_pii: true
compression:
  enabled: true
```
`memory` is disabled because coach notes live in runners.db, not Hermes memory.

### 8.4 SOUL.md
Copy `hermes/SOUL.md` from this repo into the profile's HERMES_HOME:
```bash
$ cp ~/sizweumoya/hermes/SOUL.md "$(hermes -p runner-<N> config get HERMES_HOME 2>/dev/null || echo ~/.hermes/profiles/runner-<N>)/SOUL.md"
```

### 8.5 API server key
```bash
$ hermes -p runner-<N> config set API_SERVER_KEY <RANDOM_32_CHARS>   # e.g. `openssl rand -hex 16`
$ cd ~/sizweumoya && python -m app.admin set-hermes-key --runner <N> --key <RANDOM_32_CHARS>
```

**Check:** `hermes -p runner-<N> chat -q "what is my goal?"` calls `get_my_profile` and
answers with the goal you entered.

---

## 9. Turn on multi-profile routing

Edit the **default** profile's config (`hermes config edit`):

```yaml
gateway:
  multiplex_profiles: true
  profile_routes:                     # VERIFY exact shape in multi-profile-gateways.md
    telegram:
      "<COACH_CHAT_ID>": coach
      "<YOUR_RUNNER_CHAT_ID>": runner-<N>
  default_profile: fallback           # VERIFY key; unknown chats must land in fallback
```

If your coach chat and your runner chat are the same Telegram account, use a second Telegram
account (or a group with only you) for the runner test, because one chat ID can map to one profile.

Docs: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/multi-profile-gateways.md

**Check:** `hermes config check` reports no unknown keys under `gateway`.

---

## 10. Enable the API server

The data service starts runner turns with `POST /p/<profile>/v1/runs`.

```bash
$ hermes config set API_SERVER_ENABLED true      # VERIFY name in api-server docs
$ hermes config set API_SERVER_HOST 127.0.0.1    # never expose publicly
$ hermes config set API_SERVER_PORT 8642
```
Set `HERMES_BASE_URL=http://127.0.0.1:8642` in the data service `.env`.

Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server

**Check:**
```bash
$ curl -s -X POST http://127.0.0.1:8642/p/runner-<N>/v1/runs \
    -H "Authorization: Bearer <RUNNER_API_KEY>" -H "Content-Type: application/json" \
    -d '{"input":"Say hello in five words."}'
```
returns a JSON reply. **Copy the full JSON response** and send it to Claude: the request body
and reply field in `app/hermes.py` are adjusted to match it.

---

## 11. Start the gateway as a service

```bash
$ hermes gateway setup        # choose Telegram, confirm the bot token
$ hermes gateway install      # VERIFY: installs a systemd user service; otherwise see section 11a
$ hermes status --full
```

### 11a. Manual systemd unit (only if `gateway install` does not exist)
`/etc/systemd/system/hermes-gateway.service` (as root):
```ini
[Unit]
Description=Hermes gateway
After=network-online.target

[Service]
User=<SERVICE_USER>
ExecStart=/home/<SERVICE_USER>/.local/bin/hermes gateway
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
# systemctl daemon-reload && systemctl enable --now hermes-gateway
```

**Check:** `hermes status --full` shows Telegram connected and no `needs_attention`.

---

## 12. End-to-end checks (in order)

| # | Do this | Expected |
| --- | --- | --- |
| 1 | From an unregistered Telegram account, message the bot | Sign-up text from fallback; nothing else |
| 2 | From your runner chat: "what's my goal?" | Your goal, via `get_my_profile` |
| 3 | From your runner chat: "show Sarah's runs" | Refuses; only your own data exists to it |
| 4 | From your coach chat: "list pending approvals" | Empty list |
| 5 | From your runner chat: "my calf is sore" | Kind reply + coach chat receives an alert |
| 6 | From your runner chat: "move tomorrow's run to Friday" | "Sent to your coach" + approval request in coach chat |
| 7 | In coach chat: "approve <id>" | Runner chat gets the coach's decision |
| 8 | Do a real run with HR | Debrief arrives in the runner chat within minutes |
| 9 | `curl -X POST -H "Authorization: Bearer $JOBS_TOKEN" http://127.0.0.1:8000/jobs/weekly` | Weekly summary arrives |

Record any failure with the exact message and send it to Claude.

---

## 13. Adding each new runner (repeat per runner)

1. `python -m app.admin add-runner ...` (8.1)
2. `hermes profile create runner-<N>` (8.2)
3. Paste the runner config with the new token (8.3), copy SOUL.md (8.4)
4. Set and store the API key (8.5)
5. Add `"<CHAT_ID>": runner-<N>` under `gateway.profile_routes.telegram` (9) and restart the gateway
6. Send the runner `https://<YOUR_DOMAIN>/strava/connect/<N>`
7. Ask them to message the bot "hi"; confirm they do not get the fallback text

## 14. Removing a runner

1. `python -m app.admin delete-runner --runner <N>` (deletes all their data)
2. Remove their line from `gateway.profile_routes`, restart the gateway
3. `hermes profile delete runner-<N>` (VERIFY command)

---

## Open items to confirm (send answers to Claude)

1. Exact `gateway.profile_routes` shape and the fallback/default-profile key (section 9).
2. MCP `headers` and `identity_header` key names, and the header name `identity_header` sends
   (section 7/8). It must match `MCP_PROFILE_HEADER` in the data service `.env`.
3. API-server enable keys, run request body and the JSON reply (section 10).
4. Profile create/list/delete command names (sections 5, 14).
