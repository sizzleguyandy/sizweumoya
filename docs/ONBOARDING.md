# Onboarding, the personal record, and focus

## How a new person gets in
1. They open the bot in Telegram and tap Start. Hermes declines them (they aren't registered).
2. `/jobs/join-requests` reads the gateway log (`JOIN_LOG_FILE`, `JOIN_LOG_PATTERN`) and Coach Andy
   gets "New person ... (Telegram ID ...)". He replies `add <name> <id>` or `ignore <id>` in the
   coach chat.
3. He tells the PC agent "add the approved runners": `python -m app.admin join-requests` prints the
   `onboard` command for each; then the Hermes steps and `welcome` as below.

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
3. **Watch** (runners): brand.
4. **Sync** (runners): Intervals.icu first (free; runs arrive by themselves, programme goes to
   the watch; they give only their athlete ID, never a key; see `INTERVALS.md`). Otherwise
   instructions for sending .fit files.
5. **History**: with Intervals.icu, the last 8 weeks arrive by themselves once linked;
   otherwise 6-8 weeks of files, typed gym sessions, or "starting fresh".
6. **Zones** (runners): from their Intervals.icu threshold, or a screenshot of the watch's HR
   zones -> a request Coach Andy approves (`approve <id>`), which applies and locks them.
7. **Races** (runners) and 8. **Health**: saved; injuries alert Coach Andy.
9. **Finish**: Coach Andy gets a new-runner summary. Scheduled reports and nudges start.

## Plans
- **free** (default): everything except a programme: logging (watch, Intervals.icu, Hevy, files,
  typed), feedback after every session, weekly and monthly reports, trends, paces and zones,
  TRUE PACE, races, chat. Sarah works in monitor mode.
- **programme** (the upgrade): plus a strength and/or race programme from Coach Andy (written
  with Dex), the programme form, the race-day pacing plan and the programme on their watch.
  `python -m app.admin set-tier --runner <id> --tier programme`. Programmes can't be loaded or
  drafted for a free runner.

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
notes (including profile facts and gear), races, programme form answers, plans, weekly and fitness history, alerts and
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
| `/jobs/intervals-sync` | hourly at :17 (runs, wellness, calendar) |
| `/jobs/join-requests` | every 5 minutes (new people who messaged the bot) |
