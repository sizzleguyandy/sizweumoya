# Runner Assistant Coach – data service

The data layer for a coach-led AI running assistant. It connects to Strava, parses Hevy strength
sessions, calculates stats, stores everything in `runners.db`, and gives Hermes Agent a set of
MCP tools. The head coach writes all plans; the AI gives feedback and escalates.

```
Strava ─► FastAPI service (app.main) ─► runners.db ◄─ MCP server (app.mcp_server) ◄─ Hermes profiles
                 └─► Hermes API server (/p/<profile>/v1/runs) ─► reply ─► Telegram
```

## Parts

| File | Job |
| --- | --- |
| `app/main.py` | Strava OAuth + webhook, scheduled job endpoints |
| `app/ingest.py` | Turns a Strava activity into a stored run or strength session |
| `app/hevy.py` | Parses Hevy's workout text from the activity description |
| `app/stats.py` | HR zones, drift, run type, mileage check, fitness check, tip rules |
| `app/queries.py` | All reads/writes, always scoped to one runner_id |
| `app/identity.py` | Resolves the runner from request headers (profile name + per-profile secret) |
| `app/mcp_server.py` | Tools for Hermes; runner tools plus coach-only approval tools |
| `app/hermes.py` | Starts a turn in a runner's profile and delivers the reply on Telegram |
| `app/admin.py` | Add/delete runners, coach token |
| `hermes/SOUL.md` | Assistant-coach persona for each runner profile |

## Security rules built in

- No tool takes a runner id. The runner comes from the `x-hermes-profile` header **and** a
  per-profile bearer token, both checked against `runners` (only the token hash is stored).
- Coach tools check a separate coach token.
- Coach decisions are accepted only against a stored pending approval id.
- Strava webhook verification and OAuth state are signed; `/jobs/*` need `JOBS_TOKEN`.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in, then export the variables
python -m app.admin init
uvicorn app.main:app --port 8000      # Strava + jobs
python -m app.mcp_server              # MCP on 127.0.0.1:8100/mcp
pytest -q
```

## Add a runner

```bash
python -m app.admin add-runner --name Andrew --chat-id 123456789 --max-hr 188 --goal "Sub-2 half"
```

It prints the steps: create the Hermes profile, add the chat ID to `gateway.profile_routes`,
paste the MCP config (with the one-time token) into that profile, store the profile's
`API_SERVER_KEY`, and send the runner their Strava link.

## Schedules (system cron)

```
# Sunday 18:00 weekly summary, 1st of month fitness check, daily quiet-runner check
0 18 * * 0  curl -X POST -H "Authorization: Bearer $JOBS_TOKEN" http://127.0.0.1:8000/jobs/weekly
0 7 1 * *   curl -X POST -H "Authorization: Bearer $JOBS_TOKEN" http://127.0.0.1:8000/jobs/fitness
0 8 * * *   curl -X POST -H "Authorization: Bearer $JOBS_TOKEN" http://127.0.0.1:8000/jobs/quiet-runners
```

## Recommended Hermes settings for runner profiles

```yaml
agent:
  disabled_toolsets: [terminal, web, browser]
skills:
  write_approval: true
memory:
  write_approval: true
privacy:
  redact_pii: true
```

## Still to verify against Hermes docs

1. Exact `gateway.profile_routes` syntax and the restricted fallback profile.
2. MCP config keys: `headers` and `identity_header` (name of the header carrying the profile).
3. API-server run request body and response field (isolated in `app/hermes.py`).
4. Real Hevy description format (adjust `SET_RE` in `app/hevy.py` with a real sample).

## Strava notes

- Standard tier serves 10 athletes; apply for Extended Access before launch.
- Switch `STRAVA_API_BASE` to `https://www.api-v3.strava.com` before 2027-06-01, and move
  disconnects to `oauth/revoke`.
