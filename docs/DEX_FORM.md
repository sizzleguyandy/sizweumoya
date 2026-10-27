# The Dex programme form (Google Form)

Runners ask Sarah for a programme; she gives them this form's link and their **TR3D code**.
Coach Andy imports the answers, Dex drafts the programme from them, and nothing goes live until
he approves it.

## Build it
1. In Google Forms, create a form with **exactly these questions, titles word for word** (the
   import matches the column headers to these titles). Mark the required ones as required.
2. Responses > link to a Google Sheet. In the sheet, File > Settings > Locale: **South Africa**
   (dates as dd/mm/yyyy).
3. Put the form's link on the PC: `DEX_FORM_URL=<link>` in `rac-mcp`'s environment, restart it.
   Sarah then gives the link and the runner's code when asked for a programme.
4. To import: Responses > Download responses (.csv), then on the PC
   `python -m app.admin dex-intake --file "<downloaded file>.csv"`. Rows already imported are
   skipped; rows with a problem are listed with the reason and nothing is saved for them.
   Then delete the downloaded file (it holds health answers).

To regenerate this list (e.g. after races are added): `python -m app.admin dex-form`.
A runner's code: `python -m app.admin intake-code --runner <id>`.

## Questions
1. **Your TR3D code** (Short answer, required)
2. **Your name** (Short answer, required)
3. **What is this programme for?** (Multiple choice, required)
   - A race
   - Couch to 5K (I'm new to running)
4. **Which race?** (Dropdown)
   - African Bank Soweto Marathon
   - Comrades Marathon
   - Durban International Marathon
   - Knysna Forest Marathon
   - Sanlam Cape Town Marathon
   - Two Oceans Marathon
   - Baxters Loch Ness Marathon
   - Brighton Marathon
   - Edinburgh Marathon
   - TCS London Marathon
   - Yorkshire Marathon
   - adidas Manchester Marathon
   - Another race (not in this list)
5. **Race date** (Date)
6. **If another race: its name** (Short answer)
7. **If another race: distance in km** (Short answer (number))
8. **If another race: total climb in metres (if you know it)** (Short answer (number))
9. **Your most recent race or time trial** (Multiple choice, required)
   - 5 km
   - 10 km
   - Half marathon (21.1 km)
   - Marathon (42.2 km)
   - None in the last 6 months
10. **Its finish time (h:mm:ss or mm:ss)** (Short answer)
11. **Date of that race or time trial** (Date)
12. **How many km do you run in a normal week right now?** (Short answer (number), required)
13. **Your longest run in the last month, in km** (Short answer (number), required)
14. **Which days can you run?** (Checkboxes, required)
   - Monday
   - Tuesday
   - Wednesday
   - Thursday
   - Friday
   - Saturday
   - Sunday
15. **Best day for your long run** (Dropdown, required)
   - Monday
   - Tuesday
   - Wednesday
   - Thursday
   - Friday
   - Saturday
   - Sunday
16. **Best day for a harder session** (Dropdown, required)
   - Monday
   - Tuesday
   - Wednesday
   - Thursday
   - Friday
   - Saturday
   - Sunday
17. **How fast should your training build?** (Multiple choice, required)
   - Steady (safer; best if you're newer or injury-prone)
   - Faster (experienced, consistent runners)
18. **Would you like a personal race-day pacing plan?** (Multiple choice, required)
   - Yes
   - No
19. **Where do you usually run? (town or city)** (Short answer)
20. **Any injuries, niggles or health conditions Coach Andy should know about?** (Paragraph)
21. **Anything else Coach Andy should know?** (Paragraph)

## What each answer does
- **Race / Couch to 5K**: a race plan (at least 6 weeks out) or the 12-week Couch to 5K.
- **Which race / race date**: a library race brings its distance, climb, hills, start location,
  course guide and personal splits; the form's date wins over the library's usual date.
  Another race needs its name, distance and date (climb if known).
- **Recent race or time trial**: sets the paces when no time trial is on file (within 6 months).
- **Weekly km / longest run**: used only when there are no synced runs.
- **Days**: long-run day, harder-session day; the other ticked days are the extra runs.
- **Build**: Steady = conservative, Faster = aggressive.
- **Race-day pacing plan**: Yes unlocks `get_my_race_plan` for them.
- **Location**: saved for TRUE PACE if none is saved yet.
- **Injuries / notes**: saved as a health note and shown on every Dex draft.
