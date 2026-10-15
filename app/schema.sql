-- runners.db schema. Every runner-owned table carries runner_id.

CREATE TABLE IF NOT EXISTS runners (
    runner_id         INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    telegram_chat_id  TEXT UNIQUE,            -- chat to deliver messages to
    hermes_profile    TEXT UNIQUE NOT NULL,   -- e.g. runner-1001
    mcp_token_hash    TEXT NOT NULL,          -- sha256 of this profile's MCP secret
    hermes_api_key    TEXT,                   -- this profile's API_SERVER_KEY
    tier              TEXT NOT NULL DEFAULT 'free',
    strava_athlete_id INTEGER UNIQUE,
    strava_access_token  TEXT,
    strava_refresh_token TEXT,
    strava_expires_at    INTEGER,
    max_hr            INTEGER,
    resting_hr        INTEGER,
    hr_zones          TEXT,                   -- JSON list of 4 upper bounds: Z1..Z4 (Z5 above)
    goal              TEXT,
    units             TEXT NOT NULL DEFAULT 'km',
    joined            TEXT NOT NULL DEFAULT (date('now')),
    active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    runner_id       INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    strava_id       INTEGER NOT NULL UNIQUE,
    date            TEXT NOT NULL,
    name            TEXT,
    distance_km     REAL,
    duration_s      INTEGER,
    avg_pace_s_km   REAL,
    avg_hr          REAL,
    max_hr          REAL,
    z1_s INTEGER, z2_s INTEGER, z3_s INTEGER, z4_s INTEGER, z5_s INTEGER,
    elevation_m     REAL,
    temp_c          REAL,
    hr_drift_pct    REAL,
    run_type        TEXT,                     -- easy / long / workout / race
    is_easy_valid   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strength_sessions (
    id              INTEGER PRIMARY KEY,
    runner_id       INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    strava_id       INTEGER NOT NULL UNIQUE,
    date            TEXT NOT NULL,
    title           TEXT,
    duration_s      INTEGER,
    avg_hr          REAL,
    raw_description TEXT,
    parse_ok        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strength_sets (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES strength_sessions(id) ON DELETE CASCADE,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    exercise    TEXT NOT NULL,
    set_no      INTEGER NOT NULL,
    reps        INTEGER,
    weight_kg   REAL,
    duration_s  INTEGER,
    distance_m  REAL
);

CREATE TABLE IF NOT EXISTS weekly_summary (
    runner_id           INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    week_start          TEXT NOT NULL,
    total_km            REAL,
    runs                INTEGER,
    strength_sessions   INTEGER,
    easy_pct            REAL,
    km_change_pct       REAL,
    PRIMARY KEY (runner_id, week_start)
);

CREATE TABLE IF NOT EXISTS fitness_checks (
    id              INTEGER PRIMARY KEY,
    runner_id       INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    ref_hr          REAL,
    pace_s_km       REAL,
    prev_pace_s_km  REAL,
    change_pct      REAL,
    verdict         TEXT                      -- fitter / steady / dipping / insufficient
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date        TEXT NOT NULL DEFAULT (datetime('now')),
    direction   TEXT NOT NULL,                -- out / in
    type        TEXT,                         -- debrief / weekly / fitness / chat
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tips_given (
    id          INTEGER PRIMARY KEY,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date        TEXT NOT NULL DEFAULT (date('now')),
    rule        TEXT NOT NULL,
    text        TEXT
);

CREATE TABLE IF NOT EXISTS coach_notes (
    id          INTEGER PRIMARY KEY,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date        TEXT NOT NULL DEFAULT (date('now')),
    source      TEXT,
    category    TEXT NOT NULL,                -- pattern/preference/health/life/motivation/milestone
    note        TEXT NOT NULL,
    linked_id   TEXT,
    status      TEXT NOT NULL DEFAULT 'active', -- active/resolved/outdated
    review_by   TEXT
);

CREATE TABLE IF NOT EXISTS runner_summary (
    runner_id   INTEGER PRIMARY KEY REFERENCES runners(runner_id) ON DELETE CASCADE,
    updated     TEXT NOT NULL DEFAULT (datetime('now')),
    summary     TEXT NOT NULL
);

-- Paid modules: plans are written by the coach only.
CREATE TABLE IF NOT EXISTS plans (
    id          INTEGER PRIMARY KEY,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    type        TEXT NOT NULL,                -- run / strength / recovery
    start_date  TEXT,
    end_date    TEXT,
    version     INTEGER NOT NULL DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS planned_workouts (
    id              INTEGER PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    runner_id       INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    description     TEXT NOT NULL,
    target          TEXT,                     -- JSON
    completed_strava_id INTEGER,
    status          TEXT NOT NULL DEFAULT 'planned'  -- planned/done/missed/changed
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,             -- random request id
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    plan_id     INTEGER REFERENCES plans(id),
    created     TEXT NOT NULL DEFAULT (datetime('now')),
    reason      TEXT NOT NULL,
    proposal    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending', -- pending/approved/rejected/edited
    coach_note  TEXT,
    decided_at  TEXT
);

CREATE TABLE IF NOT EXISTS coach_alerts (
    id          INTEGER PRIMARY KEY,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date        TEXT NOT NULL DEFAULT (datetime('now')),
    type        TEXT NOT NULL,                -- injury / volume_jump / quiet_runner / other
    message     TEXT NOT NULL,
    handled     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_runs_runner_date ON runs(runner_id, date);
CREATE INDEX IF NOT EXISTS idx_sets_runner_ex ON strength_sets(runner_id, exercise);
CREATE INDEX IF NOT EXISTS idx_notes_runner ON coach_notes(runner_id, status);
