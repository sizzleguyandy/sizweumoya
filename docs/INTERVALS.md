# Intervals.icu (Coach Andy first; clients need OAuth later)

Amazfit/Zepp (and Garmin, Coros, Wahoo...) can sync to Intervals.icu. Through it we:
- **push programme sessions** to the calendar, from where they go to the watch
- **pull activities** back (original files, into the normal .fit pipeline; Strava-sourced ones are skipped)
- **pull wellness**: resting HR, HRV, sleep, weight (`get_my_wellness`)

## Connect (on the PC)
```
$env:INTERVALS_API_KEY = "<key>"; python -m app.admin intervals-connect --runner 1 --athlete-id <i12345>; Remove-Item Env:INTERVALS_API_KEY
```
The athlete ID and key are on Intervals.icu > Settings > Developer. Never paste the key in a chat.

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
