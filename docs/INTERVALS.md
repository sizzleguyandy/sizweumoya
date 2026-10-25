# Intervals.icu

Amazfit/Zepp (and Garmin, Coros, Wahoo...) can sync to Intervals.icu. Through it we:
- **push programme sessions** to the calendar, from where they go to the watch
- **pull activities** back (original files, into the normal .fit pipeline; Strava-sourced ones are skipped)
- **pull wellness**: resting HR, HRV, sleep, weight (`get_my_wellness`)

## Connect (on the PC)
```
$env:INTERVALS_API_KEY = "<key>"; python -m app.admin intervals-connect --runner 1 --athlete-id <i12345>; Remove-Item Env:INTERVALS_API_KEY
```
The athlete ID and key are on Intervals.icu > Settings > Developer. Never paste the key in a chat.

Then mark it as the coach account, once:
```
python -m app.admin intervals-set-coach --runner 1
```

## Linking clients (no keys)
Clients never give us a key. Coach Andy's key reaches every athlete who has accepted him as
their coach in Intervals.icu.
1. In onboarding, Sarah walks them through: a free Intervals.icu account, connecting their
   watch's app (Garmin Connect, Zepp, COROS, Suunto, Polar, Wahoo; for Garmin, tick "upload
   planned workouts"), then sending her their athlete ID (`connect_my_intervals`). Saved as **requested**.
2. Coach Andy gets "Approval needed [id] ... link Intervals.icu athlete i...". He sends that
   athlete a coaching request in Intervals.icu, checks it's really them, then replies `approve <id>`.
   Now **approved**.
3. As soon as they accept (checked straight away, then hourly by `/jobs/intervals-sync`) it goes
   **active**: the last 8 weeks of activities and 14 days of wellness come in, any programme
   goes on their calendar, their Intervals.icu run threshold becomes a zones request (80/87/92/98%,
   for Coach Andy to approve), and both Coach Andy and the runner are told.

Garmin users are covered this way: Garmin Connect -> Intervals.icu brings their runs in, and
planned workouts go back to the watch. The direct Garmin API code stays switched off.

By hand: `intervals-link --runner N --athlete-id i...` (skips the approval) and
`intervals-check --runner N` (status, and whether access works yet).
An athlete ID already linked to someone else is refused and Coach Andy is told.

## Push the programme
```
python -m app.admin intervals-push --runner 1 --dry-run     # look first
python -m app.admin intervals-push --runner 1               # send the next 28 days
```
Re-running updates the same sessions (external_id `sizwe-<id>`), never duplicates them.
Easy and long runs go as a one-step workout ("5km Z2 HR"). For full structure add a `steps`
column to the programme CSV in Intervals.icu workout text, lines separated by `|`:
`- 15m Z2 HR|6x|- 90s Z4 HR|- 90s Z1 HR|- 10m Z2 HR`.
Set the same HR zones in Intervals.icu (Settings > Zones) as in our record so watch and Sarah agree.

## Pull
```
python -m app.admin intervals-pull --runner 1 --since 2026-10-01
```
Then schedule `POST /jobs/intervals-sync` hourly at an odd minute (e.g. :17).
Automatic debriefs for pulled activities stay off (`INTERVALS_AI_ALLOWED=0`) until the
Intervals.icu API terms have been checked.

## Keeping the calendar in step
Every programme change (a move approved by Coach Andy, `coach_move_session`, `move-session`,
`set-steps`, `load-programme`, `import-tracker`) updates the Intervals.icu calendar straight away,
and the hourly `/jobs/intervals-sync` does it again as a safety net. Entries we created whose
session was removed or whose programme was replaced are deleted; completed sessions and anything
the person added themselves are never touched.
