# Hevy sync (paid)

Gym workouts saved in Hevy arrive automatically and are stored as strength sessions
(`source='hevy'`), so lift progress, records, weekly volume, plan matching and the record file
all work as for typed or watch sessions. Warm-up sets are left out. Editing a workout in Hevy
replaces it here. Hevy's API needs **Hevy Pro**; it may change without notice.

## Connect (one person, on the PC)
1. In Hevy (hevy.com/settings?developer) generate an API key. Never paste it in a chat.
2. Put it in the environment for one command only and link it:
   ```
   $env:HEVY_API_KEY = "<key>"; python -m app.admin hevy-connect --runner 1; Remove-Item Env:HEVY_API_KEY
   ```
   It prints the webhook URL and an authorization header (shown once; only its hash is stored).
3. In Hevy > Webhooks: paste the URL and the header, press Subscribe.
4. Cloudflare tunnel: expose `https://coach.sizweumoya.com/hevy/webhook` (POST only) to
   `http://127.0.0.1:8000/hevy/webhook`.
5. Past workouts: `python -m app.admin hevy-import --runner 1 --since 2026-08-01`

Only paid tiers can connect. The API key never goes into the personal record file.
