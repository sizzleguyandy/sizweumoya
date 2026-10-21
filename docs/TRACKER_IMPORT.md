# Importing a race tracker workbook

`python -m app.admin import-tracker` loads Coach Andy's tracker workbook into one runner.

| Tab | Goes to |
| --- | --- |
| Race Info | `races`; runner's HR zones (locked), max HR, easy HR cap, weight, goal |
| Weekly Plan | one active `plans` row + `planned_workouts` (km, kind, HR range, climb). Notes under the table become a coach note |
| Run Log | `runs` with `source='sheet'` (no zone data). Sick rows become one resolved health note; Gym rows a strength session |
| Strength | `strength_sessions` + `strength_sets`, one session per date |
| Weekly Tracker | not imported: worked out from the runs |

Past planned sessions with no run: ticked in the sheet means `done`, otherwise `missed`.

When the runner later sends the `.fit` file for a run already in the sheet (same date, distance
within 10%), the file replaces the spreadsheet row and keeps its note and plan link.

Runs are matched to the plan on the same day or one day either side, preferring the earlier
session. Each run's `plan` shows distance against plan and HR against the cap
(`over_hr_cap`). Easy and long runs above the cap also get the `over_hr_cap` tip.

## Turning runner-1 (the test number) into Coach Andy's own live record

On the PC, in `C:\Users\andre\sizweumoya` with the venv active:

```
git pull
pip install -r requirements.txt
python -m app.admin reset-runner --runner 1 --name Andy --tier full
python -m app.admin import-tracker --runner 1 --file "C:\path\to\Grape_Run_21km_Tracker-3.xlsx" --replace
```

Then copy `hermes\SOUL.md` into the runner-1 profile, restart `rac-mcp` and the gateway, and
send the Zepp `.fit` files (or one `.zip` of them) from the test Telegram.
The profile, route, MCP token and API key are not touched by the reset.
