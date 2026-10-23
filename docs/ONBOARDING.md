# Onboarding, the personal record, and focus

## Adding a person (Coach Andy / the Hermes agent)

```
python -m app.admin onboard --name Tom --chat-id 123456789 [--focus running|strength|hybrid] [--goal "..."]
```
It registers the person, creates their record file, and prints the Hermes steps in the safe
order: profile -> MCP config -> API key (`set-hermes-key`) -> route -> restart -> allowlist -> restart.
Then:
```
python -m app.admin welcome --runner <id>
```
Sarah introduces herself and starts onboarding in Telegram.

## What Sarah does (one step per message)
1. **Consent**: data use, heart rate as health data, Coach Andy can see it, AI-written replies,
   deletion any time. Recorded only on a clear yes. Nothing else happens without it.
2. **Focus**: runs, lifts or both. Decides how they are coached (see below).
3. **Watch** (runners): brand, then instructions for getting .fit files off it.
4. **History**: last 6-8 weeks of files, or typed gym sessions, or "starting fresh".
5. **Zones** (runners): screenshot of the watch's HR zones -> a request Coach Andy approves
   (`approve <id>`), which applies and locks them.
6. **Races** (runners) and 7. **Health**: saved; injuries alert Coach Andy.
8. **Finish**: Coach Andy gets a new-runner summary. Scheduled reports and nudges start.

## Focus and options
| focus | coached as | weekly / monthly report |
| --- | --- | --- |
| running | runner | distance, time, zones, mileage check |
| strength | lifter (ACSM strength guidance), no running numbers | sessions, sets per muscle, lift progress |
| hybrid | both | both |

For each discipline: **monitor** (they log their own training, Sarah gives feedback) or
**guided** (a programme from Coach Andy is on file; Sarah takes them through it and compares
every session with the plan). Load a programme:
```
python -m app.admin load-programme --runner <id> --file programme.csv [--replace]
```
Write programmes in **`templates/programme_template.xlsx`** (sheet "Programme", one row per
session, drop-downs for type and kind, help on each heading, a filled "Example" sheet, optional
"Races" sheet). A CSV with the same columns also works:
`date,type,description,km,kind,hr_low,hr_high,climb_m,exercises,steps`. Running plans can also come from the tracker workbook (`import-tracker`).

## The personal record
`runners.db` is the single source of truth. Each person also has their own complete file,
`data/runners/<profile>.db`, rebuilt after every change and nightly (`POST /jobs/records`):
profile, runs, per-km splits, strength sessions and every set, every report and message sent,
notes (including profile facts and gear), races, plans, weekly and fitness history, alerts and
approvals. Secrets are never copied into it.

Each file also has ready-made views, `weekly_km`, `monthly_km`, `personal_bests` and
`lift_records`, and a `latest_insights` table (progress status, load flag, taper, lift records
as worked out at export time). People can ask Sarah for any of these, or for the file itself
(`send_my_data_file`, sent to their chat).

Sarah saves with `save_report` (every debrief and report) and `add_coach_note` (facts), and
looks anything up with `get_my_record(section, since, until)`.

```
python -m app.admin export-record --runner <id> [--to copy.db]     # a copy, e.g. for the person
python -m app.admin offboard --runner <id> [--keep-copy final.db]  # delete everything
```

## Jobs to schedule
| Job | When |
| --- | --- |
| `/jobs/weekly` | Sunday 18:00 |
| `/jobs/monthly` | 1st, 07:00 |
| `/jobs/session-preview` | daily 18:30 |
| `/jobs/nudge` | daily 17:00 (after 5 quiet days, at most every 5 days) |
| `/jobs/quiet-runners` | daily 08:00 (Coach Andy, 7+ days) |
| `/jobs/records` | daily 02:30 (before the 03:00 backup) |
