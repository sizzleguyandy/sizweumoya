"""Build templates/programme_template.xlsx:  python templates/make_template.py"""
from datetime import date

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

COLS = [("date", 12, "Session date. Type it as a date (e.g. 3 Nov 2026) or YYYY-MM-DD."),
        ("type", 10, "run or strength"),
        ("description", 42, "What the athlete sees, e.g. 'Hill reps 6 x 90s'. Required."),
        ("km", 7, "Runs: planned distance in km (optional)."),
        ("kind", 11, "Runs: easy, long, workout, benchmark (time trial) or race."),
        ("hr_low", 8, "Optional: bottom of the target heart rate range (bpm)."),
        ("hr_high", 8, "Optional: top of the range. With no hr_low it is a cap ('under 144')."),
        ("climb_m", 9, "Runs: target climb in metres (optional)."),
        ("exercises", 40, "Strength: exercises separated by ; e.g. 'Squat 4x6 70kg; RDL 3x8 60kg'"),
        ("steps", 48, "Optional watch workout (Intervals.icu text), steps separated by | e.g. "
                      "'- 15m Z2 HR|6x|- 90s Z4 HR|- 90s Z1 HR|- 10m Z2 HR'")]
HEAD = PatternFill("solid", fgColor="1F3A5F")
BOLD_WHITE = Font(bold=True, color="FFFFFF")

EXAMPLE = [
    (date(2026, 11, 3), "run", "Easy run", 5, "easy", None, 144, None, None, "- 5km Z2 HR"),
    (date(2026, 11, 4), "strength", "Lower body A", None, None, None, None, None,
     "Squat 4x6 70kg; RDL 3x8 60kg; Calf raise 3x15", None),
    (date(2026, 11, 5), "run", "Hill reps 6 x 90s", 7, "workout", 160, 175, 120, None,
     "- 15m Z2 HR|6x|- 90s Z4 HR|- 90s Z1 HR|- 10m Z2 HR"),
    (date(2026, 11, 6), "run", "Easy run", 5, "easy", None, 144, None, None, "- 5km Z2 HR"),
    (date(2026, 11, 8), "run", "Long run, last 20 min at half-marathon effort", 14, "long", 152, 163, 200, None,
     "- 10km Z2 HR|- 20m Z4 HR"),
    (date(2026, 11, 12), "run", "5K time trial (flat)", 8, "benchmark", None, None, None, None,
     "- 10m Z2 HR|- 5km Z5 HR|- 10m Z1 HR"),
    (date(2026, 11, 15), "run", "RACE: Grape Run 21.1 km", 21.1, "race", None, None, 445, None, None),
]


def header(ws, cols):
    for i, (name, width, note) in enumerate(cols, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.fill, c.font = HEAD, BOLD_WHITE
        if note:
            c.comment = Comment(note, "Sizwe Umoya")
        ws.column_dimensions[c.column_letter].width = width
    ws.freeze_panes = "A2"


def programme_sheet(ws, rows=()):
    header(ws, COLS)
    for r in rows:
        ws.append(list(r))
    for row in ws.iter_rows(min_row=2, max_row=400, max_col=1):
        row[0].number_format = "yyyy-mm-dd"
    for col in ("C", "I", "J"):
        for row in ws.iter_rows(min_row=2, max_row=400, min_col=ord(col) - 64, max_col=ord(col) - 64):
            row[0].alignment = Alignment(wrap_text=True, vertical="top")
    dv_type = DataValidation(type="list", formula1='"run,strength"', allow_blank=True)
    dv_kind = DataValidation(type="list", formula1='"easy,long,workout,benchmark,race"', allow_blank=True)
    dv_num = DataValidation(type="decimal", operator="between", formula1="0", formula2="300", allow_blank=True)
    for dv, rng in ((dv_type, "B2:B400"), (dv_kind, "E2:E400"), (dv_num, "D2:D400"), (dv_num, "F2:H400")):
        ws.add_data_validation(dv)
        dv.add(rng)


wb = openpyxl.Workbook()
how = wb.active
how.title = "How to use"
lines = [
    "SIZWE UMOYA - PROGRAMME TEMPLATE", "",
    "1. Fill in the 'Programme' sheet: one row per session. Don't rename the sheet or the column headings.",
    "2. Optional: add races on the 'Races' sheet (they drive Sarah's countdown and taper).",
    "3. Save, then on the PC:  python -m app.admin load-programme --runner <id> --file <this file>",
    "   Use --replace to replace the athlete's current programme of the same type.",
    "4. The programme goes on the athlete's Intervals.icu calendar and watch automatically.",
    "", "Columns (hover a heading in the Programme sheet for help):",
    "  date         the day of the session",
    "  type         run or strength",
    "  description  what the athlete sees (required)",
    "  km           runs: planned distance",
    "  kind         runs: easy, long, workout, benchmark (time trial), race",
    "  hr_low/high  runs: target heart rate range; only hr_high = a cap (e.g. easy under 144)",
    "  climb_m      runs: target climb in metres",
    "  exercises    strength: 'Squat 4x6 70kg; RDL 3x8 60kg' (separated by ;)",
    "  steps        optional watch workout, steps separated by |  e.g.  - 15m Z2 HR|6x|- 90s Z4 HR|- 90s Z1 HR",
    "               Zones refer to the athlete's zones in Intervals.icu. Easy and long runs without steps",
    "               are sent as a simple '5km Z2 HR' workout; other runs without steps go as notes only.",
    "", "Rest days: just leave them out.",
    "See the 'Example' sheet for a filled-in week (it is never loaded).",
]
for l in lines:
    how.append([l])
how["A1"].font = Font(bold=True, size=14)
how.column_dimensions["A"].width = 110

programme_sheet(wb.create_sheet("Programme"))
races = wb.create_sheet("Races")
header(races, [("name", 30, "Race name"), ("date", 12, "Race day"), ("distance_km", 12, "e.g. 21.1"),
               ("ascent_m", 10, "Total climb (optional)"), ("note", 50, "Start time, surface, route (optional)")])
for row in races.iter_rows(min_row=2, max_row=50, min_col=2, max_col=2):
    row[0].number_format = "yyyy-mm-dd"
programme_sheet(wb.create_sheet("Example"), EXAMPLE)

wb.save("templates/programme_template.xlsx")
print("written")
