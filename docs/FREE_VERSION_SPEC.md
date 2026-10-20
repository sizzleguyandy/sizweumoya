# Free version spec: Sarah, Coach Andy's assistant

Design decisions for the free version. **Status: designed, not built** unless marked.
The full project brief lives in the Claude doc "Runner Assistant Coach – System Brief".

## 1. Decisions

| Topic | Decision |
| --- | --- |
| Names | Coach is **Coach Andy**; the assistant is **Sarah** |
| Scope | Feedback only. Coach Andy writes all plans (paid add-ons) |
| Weekly summary | Sunday evening |
| Tone | Supportive, natural language, almost no emojis, one tip at most |
| Short runs | Under 3 km **or** under 20 minutes: no debrief; stored and counted in the weekly summary |
| Chat | Unlimited in the free version; any running topic |
| Stats | Read-only for runners; only Coach Andy can override data |
| Extra context | Optional Strava notes; never required |

## 2. Onboarding

Strava first, so Sarah only asks what the data cannot show.

1. **Access.** Runner messages the bot and gets the decline message. Coach Andy runs one
   `onboard` command: register → profile → route → restart → allowlist → restart.
2. **Welcome** (sent automatically by the onboard command) with the Strava link.
3. **Strava connects** → import last 8 weeks of runs and Hevy sessions, read HR zones from
   Strava (`/athlete/zones`).
4. **Two questions:** any races planned (name, date, distance)? Any injuries or niggles?
5. **Baseline snapshot** to the runner; **new-runner summary** to Coach Andy.
6. Normal feedback starts.

**Injury (onboarding or later):** health note, Coach Andy alerted, careful mode on (mileage
warning at +5% instead of +10%, easy-running tips), check-in after 7 days.

## 3. Messages (exact voice)

**Welcome**
> Hi Tom, welcome! I'm Sarah, Coach Andy's assistant. After each run and strength session I'll
> send you a short note on how it went, plus a weekly summary on Sunday evenings and a fitness
> check once a month. Coach Andy looks after your training plans; I'm here to keep an eye on
> things and help along the way. To get started, connect your Strava here: [link]. I'll only use
> your training data for coaching, and you can ask me to delete it any time.

**After Strava connects**
> Thanks, you're connected. I've had a look at your last eight weeks. Two quick questions before
> I share what I found: do you have any races coming up?

> And are you carrying any injuries or niggles at the moment?

**Baseline**
> Here's where you're at. Over the last eight weeks you've averaged about 28 km a week across
> three or four runs, with a longest run of 16 km. Around three quarters of your running has
> been easy, which is a good base. Your easy pace sits around 6:10 per km at a heart rate of
> about 142. With the Durban 10K six weeks away, the one thing I'd focus on is keeping those easy
> days genuinely easy. That's where a lot of the fitness gets built.

**Run debrief**
> Nice one, 8.2 km at 5:42 per km. Most of that sat comfortably in zone 2, exactly where an easy
> run should be. Your heart rate crept up a little in the second half, which is normal. Next time
> try starting the first couple of kilometres a touch slower, and you'll likely feel stronger at
> the end.

**Strength debrief**
> Good session. Squats are up to 65 kg for 3x8, five kilos more than last time, so that's real
> progress. RDLs and calf raises held steady. I'd stay at 65 on squats next time and make every
> rep feel solid before adding more.

**Weekly summary (Sunday evening)**
> Here's your week. You ran 32 km over four runs, plus two short shakeouts, and fitted in two
> strength sessions, which is great consistency. That's a 14% jump on last week, so I'd hold
> around this level next week and let your body catch up. About 72% of your running was easy;
> the sweet spot is closer to 80. If you can make Tuesday's run properly relaxed, you'll be right
> there. Five weeks to Durban.

Mileage wording: below −20% "a lighter week, fine if planned"; −20% to +10% "a steady build";
above +10% (careful mode: +5%) "a big jump, hold around this level".

**Monthly fitness check**
- Fitter: "Good news. Your easy pace at the same heart rate has dropped from 6:15 to 6:02 per km
  over the last month. Put simply, the same effort is now carrying you faster."
- Steady: "Your fitness is holding steady this month. That's completely normal; gains come in
  waves. Keep stacking consistent weeks and it'll move."
- Dipping: "Your easy pace has slowed a little this month. That's usually tiredness, heat or a
  busy few weeks rather than lost fitness. If you're feeling run down, let me know."
- Not enough data: "I need at least three easy runs in each four-week window to tell."

**Injury or niggle**
> Sorry to hear that. Can you tell me a bit more: where exactly, when it started, and whether
> it's a dull ache or something sharper? I've let Coach Andy know. Until Coach Andy has had a
> look, I'd ease off rather than push through. If it's sharp, swollen, or changing the way you
> run, it's worth seeing a physio.

Seven days later: "Hi Tom, just checking in. How's the calf feeling this week?"

**Plan question**
> That's one for Coach Andy. Want me to pass your question on?

**To Coach Andy**
- New runner: "Tom is set up. 28 km a week, 74% easy, strength once a week. Racing the Durban
  10K in 6 weeks. Mentioned a sore left Achilles, so careful mode is on."
- Alert: "Alert for Tom: left calf sore, sharp, since Tuesday's run."
- Quiet runners: "No activity for 7+ days: Tom, Lisa."

## 4. Questions about reports

- Every report is saved with what it was based on (activity, week, rule that fired).
- A runner can reply to any report; Sarah explains with the real numbers.
- Drill-down tools (to build): run splits per km, the week's easy/hard breakdown, fitness-check
  workings, the runner's HR zones, a plain-English glossary.
- Disagreement: acknowledge, explain the numbers, offer to pass it to Coach Andy. Repeated
  pushback is flagged to Coach Andy.
- **Runners cannot change stats.** Sarah suggests a Strava note or passes it to Coach Andy.

## 5. Optional Strava notes

| Strava field | Use |
| --- | --- |
| Description | Free text, e.g. "weather: hot. energy: 2. sleep: poor." |
| Perceived exertion (1–10) | Felt effort vs heart rate |
| Workout type (race / long run / workout) | Correct debrief without guessing |
| Treadmill / indoor toggle | Pace left out of fitness comparisons |
| Private notes | Read by Sarah, not public; useful for niggles |

Keywords: `weather`, `energy`, `sleep`, `niggle`, `hr bad`, `treadmill`.
- `niggle` → injury flow. `hr bad` → run flagged to Coach Andy (not excluded).
- Hot weather → no drift tip; reduced weight in the fitness check.
- Strava sends an update event when a runner edits an activity; Sarah re-reads it and may send a
  short follow-up.
- Mentioned once at onboarding; afterwards only when a run looked unusual.

## 6. Build list for this spec (not started)

1. `onboard` / `offboard` commands automating the safe order
2. Welcome sent by the onboard command
3. Tools: record consent, save races and injury answers, careful-mode flag (fixed fields only)
4. Strava connect → 8-week backfill, HR zones from Strava, baseline, new-runner summary to Coach Andy
5. Short-run rule (no debrief under 3 km or 20 min)
6. Read Strava description, perceived exertion, workout type, trainer flag, private note
7. Reports saved with their basis; drill-down tools and glossary
8. 7-day injury check-in
9. Runner data commands: show my data, delete my data

## 7. Free version goals

Three jobs: show whether the runner is progressing, catch overtraining and injury risk early,
and get them to race day fresh.

### 7.1 Progress status (never red)

| Status | Meaning | Sarah says |
| --- | --- | --- |
| Green | Progressing | "You're getting fitter." |
| Yellow | Holding steady | "Holding steady; gains come in waves." |
| Grey | No progress yet, or a slight dip | "No change this month; usually tiredness or a busy few weeks." |

Signals and stagnation rules:

| Signal | Progress | Stagnation |
| --- | --- | --- |
| Easy pace at same HR (monthly) | Faster | Unchanged 2–3 months running |
| Weekly distance (4-week average) | Gradually rising | Flat 6+ weeks |
| Longest run | Growing towards race needs | Same 6+ weeks |
| Easy/hard balance | Close to 80/20 | Mostly zone 3 |
| Hevy lifts | Weight or reps up | Same weight 4+ sessions |

On stagnation Sarah names the likely reason from the data and Coach Andy is told.

### 7.2 Load and injury flags (weekly traffic light)

Signs:
- Weekly distance up more than 10% (5% in careful mode)
- Last week above 1.5x the 4-week average
- 7+ days in a row without rest
- Easy running below 70%
- Heart rate higher at the same pace for 1–2 weeks
- Fitness dipping while distance rises
- Repeated low energy / poor sleep in Strava notes, or high perceived effort on easy runs
- Any niggle (chat or Strava note); long run above ~35% of the week; sudden stop after a hard week

| Level | Trigger | Who hears |
| --- | --- | --- |
| Green | No signs | Nobody; normal report |
| Amber | One or two signs | **Runner only**, gently, in the weekly summary |
| Red | Several signs, or a niggle on high load | **Coach Andy immediately**; runner gets a kind, calm message |

Amber example: "You've done three hard weeks back to back and your heart rate is running a bit
higher than usual on easy runs. That's usually your body asking for an easier week."

### 7.3 Race taper (always suggested when a race is planned)

**Base mileage** = average of the last 3 full weeks before the taper, ignoring unusually light weeks.

| Race | Taper starts | Week −3 | Week −2 | Race week (excl. race) | Last long run |
| --- | --- | --- | --- | --- | --- |
| 5K | 7 days out | — | — | 70% of base | Normal, 7+ days out |
| 10K | 10 days out | — | 85% of base | 60% of base | 10 days out, ~75% of usual |
| Half marathon | 14 days out | — | 75% of base | 50% of base | 10–12 days out, ~70% of usual |
| Marathon | 21 days out | 80% of base | 65% of base | 40% of base | 21 days out: the longest run |

Rules:
- Keep 1–2 short race-pace efforts each taper week (cut distance, not all intensity).
- Base under 20 km/week: shorter taper (half 7–10 days, marathon 14 days).
- Careful mode: a further 10% volume reduction.
- Sarah tracks each week against target, sends a race-week note (sleep, fuelling, pacing) and a
  post-race message (result, personal best, easy recovery days).
- Coach Andy gets a copy of every suggested taper; a paid plan overrides it.

Example (half marathon, base 40 km/week):
> Two weeks to Durban, so this is where we ease off and let the work sink in. Based on your recent
> average of 40 km a week, here's a simple taper. This week: about 30 km, with your last longer run
> around 14 km. Race week: about 20 km, all easy apart from a couple of short bursts at race pace.
> The fitness is already built; now it's about arriving fresh.

### 7.4 Gauges (text, first version)

Sent inside `<pre>` (monospace) in Telegram, ten blocks wide (▰ filled, ▱ empty):

```
Fitness   ▰▰▰▰▰▰▰▱▱▱  Progressing
Load      ▰▰▰▰▱▱▱▱▱▱  Comfortable
Taper     ▰▰▰▰▰▰▰▰▱▱  On track
```

- Fitness: green ≥ 7 blocks, yellow 4–6, grey ≤ 3 (driven by the progress status).
- Load: blocks scale with last week vs 4-week average; labels Easy / Comfortable / High (amber) / Very high (red).
- Taper: share of the taper plan completed; labels On track / Ease off more / Running light.

Later: animated speedometer image (monthly report, taper start), then a Telegram mini app
dashboard (paid version).

### 7.5 Build list additions

10. Progress status and stagnation detection
11. Weekly load/injury traffic light with amber → runner, red → Coach Andy
12. Race storage and taper calculator, weekly taper tracking, race-week and post-race messages
13. Text gauges in weekly and monthly reports
