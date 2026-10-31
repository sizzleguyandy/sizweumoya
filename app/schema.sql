-- runners.db schema. Every runner-owned table carries runner_id.

CREATE TABLE IF NOT EXISTS runners (
    runner_id         INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    telegram_chat_id  TEXT UNIQUE,            -- chat to deliver messages to
    hermes_profile    TEXT UNIQUE NOT NULL,   -- e.g. runner-1001
    mcp_token_hash    TEXT NOT NULL,          -- sha256 of this profile's MCP secret
    hermes_api_key    TEXT,                   -- this profile's API_SERVER_KEY
    tier              TEXT NOT NULL DEFAULT 'free',
    max_hr            INTEGER,
    resting_hr        INTEGER,
    hr_zones          TEXT,                   -- JSON list of 4 upper bounds: Z1..Z4 (Z5 above)
    zones_locked      INTEGER NOT NULL DEFAULT 0, -- 1 = set by Coach Andy; never auto-updated
    easy_hr_cap       INTEGER,                -- coach-set ceiling for easy and long runs
    focus             TEXT NOT NULL DEFAULT 'running', -- running / strength / hybrid
    watch             TEXT,                   -- brand the runner uses (garmin, coros, ...)
    consent_at        TEXT,                   -- when the runner agreed to the privacy terms
    onboarded_at      TEXT,                   -- when onboarding finished
    location_name     TEXT,                   -- where they usually run (for TRUE PACE weather)
    latitude          REAL,
    longitude         REAL,
    elevation_m       REAL,
    intake_code       TEXT UNIQUE,            -- code they type into the Dex programme form
    weight_kg         REAL,
    goal              TEXT,
    units             TEXT NOT NULL DEFAULT 'km',
    joined            TEXT NOT NULL DEFAULT (date('now')),
    active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    runner_id       INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    activity_key    TEXT NOT NULL UNIQUE,     -- 'fit:<sha256>' for uploaded files
    source          TEXT NOT NULL DEFAULT 'fit',
    date            TEXT NOT NULL,
    name            TEXT,
    note            TEXT,                     -- runner's caption
    treadmill       INTEGER NOT NULL DEFAULT 0,
    device          TEXT,                     -- e.g. "Forerunner 255" (Garmin attribution)
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

-- Per-km splits from the file's per-second streams.
CREATE TABLE IF NOT EXISTS run_splits (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    km          INTEGER NOT NULL,             -- 1, 2, ... (last one may be partial)
    distance_m  REAL NOT NULL,
    duration_s  REAL NOT NULL,
    pace_s_km   REAL,
    avg_hr      REAL,
    max_hr      REAL
);

CREATE TABLE IF NOT EXISTS strength_sessions (
    id              INTEGER PRIMARY KEY,
    runner_id       INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    activity_key    TEXT NOT NULL UNIQUE,     -- 'fit:<sha256>' or 'text:<sha256>'
    source          TEXT NOT NULL DEFAULT 'fit', -- fit / text / hevy / garmin
    device          TEXT,
    date            TEXT NOT NULL,
    title           TEXT,
    note            TEXT,
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
    flag_level          TEXT,                 -- green / amber / red (load and injury)
    flags               TEXT,                 -- JSON list of the signs
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
    type        TEXT,                         -- debrief / weekly / monthly / fitness / coach_reply / ...
    text        TEXT NOT NULL,
    basis       TEXT                          -- JSON: what the report was based on
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
    completed_activity_key TEXT,
    status          TEXT NOT NULL DEFAULT 'planned'  -- planned/done/missed/changed
);

CREATE TABLE IF NOT EXISTS races (
    id          INTEGER PRIMARY KEY,
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    date        TEXT NOT NULL,
    distance_km REAL,
    ascent_m    REAL,
    note        TEXT,
    preset_id   TEXT                      -- race library id (app/racelib), when it's a library race
);

-- Runs Coach Andy deleted (e.g. halves of a run joined into one): never imported again,
-- whichever way the file comes back (Intervals.icu sync, a re-sent file).
CREATE TABLE IF NOT EXISTS deleted_activities (
    activity_key TEXT PRIMARY KEY,
    runner_id    INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date         TEXT,
    distance_km  REAL,
    deleted_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Answers from the Dex programme form (Google Form), one row per submission.
CREATE TABLE IF NOT EXISTS dex_intake (
    id           INTEGER PRIMARY KEY,
    runner_id    INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    submitted_at TEXT NOT NULL,
    answers      TEXT NOT NULL,           -- JSON, normalised (see app/intake.py)
    imported_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (runner_id, submitted_at)
);

-- Hevy connection per person: their own API key and the secret Hevy sends with each webhook.
-- Never copied into the personal record file.
-- Garmin Connect link per person (the Garmin userId that push notifications carry).
CREATE TABLE IF NOT EXISTS garmin_links (
    runner_id       INTEGER PRIMARY KEY REFERENCES runners(runner_id) ON DELETE CASCADE,
    garmin_user_id  TEXT NOT NULL UNIQUE,
    connected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    permissions     TEXT                           -- JSON list from Garmin's permission notices
);

-- Intervals.icu connection per person (personal API key for now; OAuth for clients later).
CREATE TABLE IF NOT EXISTS intervals_links (
    runner_id      INTEGER PRIMARY KEY REFERENCES runners(runner_id) ON DELETE CASCADE,
    athlete_id     TEXT NOT NULL,
    api_key        TEXT NOT NULL,          -- '' when linked through Coach Andy's account
    -- via_coach: read and written with Coach Andy's key (the athlete accepted him as coach).
    -- is_coach: this row's key is Coach Andy's own, used for every via_coach athlete.
    via_coach      INTEGER NOT NULL DEFAULT 0,
    is_coach       INTEGER NOT NULL DEFAULT 0,
    -- requested (runner gave their ID) -> approved (Coach Andy confirmed it is them) -> active
    status         TEXT NOT NULL DEFAULT 'active',
    last_sync      TEXT,
    connected_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Daily wellness from Intervals.icu (resting HR, HRV, sleep, weight).
CREATE TABLE IF NOT EXISTS wellness (
    runner_id      INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    date           TEXT NOT NULL,
    resting_hr     REAL,
    hrv            REAL,
    sleep_s        INTEGER,
    sleep_score    REAL,
    weight_kg      REAL,
    source         TEXT NOT NULL DEFAULT 'intervals',
    PRIMARY KEY (runner_id, date)
);

CREATE TABLE IF NOT EXISTS hevy_links (
    runner_id      INTEGER PRIMARY KEY REFERENCES runners(runner_id) ON DELETE CASCADE,
    api_key        TEXT NOT NULL,
    secret_hash    TEXT NOT NULL UNIQUE,          -- sha256 of the webhook authorization secret
    connected_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,             -- random request id
    runner_id   INTEGER NOT NULL REFERENCES runners(runner_id) ON DELETE CASCADE,
    plan_id     INTEGER REFERENCES plans(id),
    created     TEXT NOT NULL DEFAULT (datetime('now')),
    reason      TEXT NOT NULL,
    proposal    TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'plan', -- plan / zones
    payload     TEXT,                         -- JSON for kinds the system applies (zones)
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
CREATE INDEX IF NOT EXISTS idx_splits_run ON run_splits(run_id);
CREATE INDEX IF NOT EXISTS idx_messages_runner ON messages(runner_id, date);
CREATE INDEX IF NOT EXISTS idx_sets_runner_ex ON strength_sets(runner_id, exercise);
CREATE INDEX IF NOT EXISTS idx_notes_runner ON coach_notes(runner_id, status);
