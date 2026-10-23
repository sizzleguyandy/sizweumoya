# Garmin Connect (Starter access): activities arrive automatically

Garmin's program documents are confidential: keep them out of this repo. This page only
describes our own setup.

## What happens
1. A linked person syncs a run or strength session. Garmin sends a notice to
   `https://coach.sizweumoya.com/garmin/activity-files`.
2. We answer at once, then download the original .fit (once) and store it through the same
   pipeline as a file sent to the bot: duplicates, splits, plan matching, trends, record.
3. Each activity keeps its device name for the "Garmin [device model]" attribution.
4. `GARMIN_AI_ALLOWED=1` turns on Sarah's automatic debrief for Garmin activities. Leave it at
   `0` until Garmin has confirmed in writing that AI feedback is allowed (agreement 15.11).
5. `/garmin/deregistration` and `/garmin/permissions` stop syncing when someone disconnects.

Starter access has no history: new people still send their last weeks as files at onboarding.

## Setup on the PC (just Coach Andy for now)
1. `.env` for rac-web: `GARMIN_CLIENT_ID=<client id>` (copied file-to-file). The client
   secret is not needed yet; never paste it anywhere else.
2. Cloudflare tunnel: expose `/garmin/*` (POST only) to `http://127.0.0.1:8000`. Nothing else.
3. Garmin API tools > Endpoint configuration: enter the three URLs above (activity files,
   deregistration, user permissions) and enable them. New domains go through a security
   review that takes 24-48 hours.
4. Garmin API tools > User Authorization tool: authorise your own Garmin account and note the
   Garmin user ID, then `python -m app.admin garmin-link --runner 1 --user-id <id>`.
5. Record a short run or sync an activity; check it appears with `source='garmin'`.

The OAuth 2.0 sign-in flow for other people comes next, once Garmin's OAuth 2.0 document is
available.
