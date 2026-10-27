# Dex, the run brain

Dex writes running programmes. Sarah never does: she asks Dex and passes the result on.
Dex is code, not a model: the TR3D Run Coach engine (copied into `app/dex/engine/` from
`sizzleguyandy/run_coach`, `coach_core/engine`) builds the plan: Daniels VO2X paces, phases,
volume curve, quality sessions, hills and taper. Dex adds Coach Andy's heart-rate targets and
turns it into programme rows (the same columns as `templates/programme_template.xlsx`).

## What it uses
- **Race**: the runner's next race on file (or one named): distance -> 5k/10k/half/marathon/ultra,
  climb per km -> low/medium/high hilliness.
- **Weekly km**: average of the last 6 weeks of runs (or given).
- **Fitness (VDOT)**: best race or time trial of 3 km+ (or given).
- **Days**: one long-run day, one quality day, extra days (default Sun / Tue / Thu).
- **Profile**: conservative (default) or aggressive (faster build).
- **Heart rate**: easy and long runs use the runner's easy cap; threshold sessions zone 4;
  intervals and long hill repeats zone 5; short reps and sprints go by pace or effort only.

Rules: at least **6 weeks** to the race (shorter blocks are written by hand), at most 24 (it
starts later). Weeks run Monday to Sunday and the race always lands on race day.

## Flow
1. Coach Andy asks in the coach chat ("Dex, plan Tom for the City 10K, runs Tue Thu Sat Sun")
   -> `coach_dex_draft`. Or on the PC:
   `python -m app.admin dex-draft --runner 2 --extra-days Thu,Sat [--propose]`.
2. He gets the summary, the first two weeks and the Excel file (approval kind `programme`).
   Nothing changes yet.
3. `approve <id>` loads it as the run programme (replacing the current one), sends it to the
   Intervals.icu calendar and tells the runner. To change sessions first, edit the Excel file
   and load it with `load-programme --replace` instead.

## Next
2. Runners ask Sarah for a programme -> a Dex draft for Coach Andy.
3. Sunday adjustment of next week (the engine's adaptation rules) as an approval.
4. Revisions on request (move, cut volume, add a rest week).
