-- household_<id>.db: children, routines, steps, pars, runs, segments, events,
-- awards, records, streaks (spec §10). Timestamps are epoch milliseconds.

CREATE TABLE IF NOT EXISTS children (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    colour       TEXT NOT NULL,
    avatar       TEXT,
    nfc_token    TEXT UNIQUE,
    birth_year   INTEGER,
    display_mode TEXT NOT NULL DEFAULT 'ring_numeric'
                 CHECK (display_mode IN ('ring', 'ring_numeric')),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS routines (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    schedule_cron TEXT,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS steps (
    id                TEXT PRIMARY KEY,
    routine_id        TEXT NOT NULL REFERENCES routines(id),
    position          INTEGER NOT NULL,
    title             TEXT NOT NULL,
    icon              TEXT,
    kind              TEXT NOT NULL CHECK (kind IN ('task', 'gate')),
    floor_seconds     INTEGER,
    on_reject_step_id TEXT REFERENCES steps(id),
    active            INTEGER NOT NULL DEFAULT 1,
    -- a gate has no floor and cannot be the first step (spec §4)
    CHECK (kind = 'task' OR (floor_seconds IS NULL AND position > 0)),
    CHECK (kind = 'gate' OR on_reject_step_id IS NULL)
);

CREATE TABLE IF NOT EXISTS pars (
    id              TEXT PRIMARY KEY,
    child_id        TEXT NOT NULL REFERENCES children(id),
    step_id         TEXT NOT NULL REFERENCES steps(id),
    par_seconds     INTEGER NOT NULL,
    grace_seconds   INTEGER NOT NULL,
    basis           TEXT NOT NULL CHECK (basis IN ('none', 'best', 'median')),
    effective_from  INTEGER NOT NULL,
    computed_from_n INTEGER NOT NULL DEFAULT 0,
    frozen          INTEGER NOT NULL DEFAULT 0,
    superseded_at   INTEGER
);
-- exactly one live par per (child, step)
CREATE UNIQUE INDEX IF NOT EXISTS ix_pars_live
    ON pars (child_id, step_id) WHERE superseded_at IS NULL;

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    routine_id      TEXT NOT NULL REFERENCES routines(id),
    state           TEXT NOT NULL
                    CHECK (state IN ('open', 'active', 'paused', 'closed', 'abandoned')),
    started_by      TEXT,
    started_at      INTEGER NOT NULL,
    closed_at       INTEGER,
    paused_seconds  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_children (
    run_id        TEXT NOT NULL REFERENCES runs(id),
    child_id      TEXT NOT NULL REFERENCES children(id),
    state         TEXT NOT NULL
                  CHECK (state IN ('checked_in', 'active', 'completed')),
    checked_in_at INTEGER,
    completed_at  INTEGER,
    total_seconds INTEGER,
    stars         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, child_id)
);

CREATE TABLE IF NOT EXISTS segments (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(id),
    child_id         TEXT NOT NULL REFERENCES children(id),
    step_id          TEXT NOT NULL REFERENCES steps(id),
    kind             TEXT NOT NULL CHECK (kind IN ('task', 'gate')),
    position         INTEGER NOT NULL,
    first_started_at INTEGER,
    ended_at         INTEGER,
    elapsed_seconds  INTEGER,
    gate_wait_seconds INTEGER,
    par_seconds      INTEGER,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    rejection_count  INTEGER NOT NULL DEFAULT 0,
    state            TEXT NOT NULL
                     CHECK (state IN ('pending', 'active', 'gate_open',
                                      'done', 'skipped', 'incomplete')),
    stars            INTEGER NOT NULL DEFAULT 0,
    quality_stars    INTEGER,
    resolution       TEXT CHECK (resolution IN ('approved', 'rejected', 'auto_approved')),
    reviewed_by      TEXT,
    reviewed_at      INTEGER,
    note             TEXT,
    reconciled       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS segment_attempts (
    id              TEXT PRIMARY KEY,
    segment_id      TEXT NOT NULL REFERENCES segments(id),
    attempt_no      INTEGER NOT NULL,
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER,
    elapsed_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    run_id      TEXT REFERENCES runs(id),
    child_id    TEXT REFERENCES children(id),
    type        TEXT NOT NULL,
    payload_json TEXT,
    occurred_at INTEGER NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('kiosk', 'parent', 'system'))
);

CREATE TABLE IF NOT EXISTS records (
    child_id     TEXT NOT NULL REFERENCES children(id),
    routine_id   TEXT NOT NULL REFERENCES routines(id),
    step_id      TEXT REFERENCES steps(id),
    best_seconds INTEGER NOT NULL,
    achieved_at  INTEGER NOT NULL,
    run_id       TEXT REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS streaks (
    child_id             TEXT NOT NULL REFERENCES children(id),
    routine_id           TEXT NOT NULL REFERENCES routines(id),
    current              INTEGER NOT NULL DEFAULT 0,
    best                 INTEGER NOT NULL DEFAULT 0,
    last_qualifying_date TEXT,
    PRIMARY KEY (child_id, routine_id)
);

CREATE TABLE IF NOT EXISTS awards (
    id           TEXT PRIMARY KEY,
    child_id     TEXT NOT NULL REFERENCES children(id),
    kind         TEXT NOT NULL CHECK (kind IN ('steady', 'record', 'streak_milestone')),
    period_start INTEGER,
    period_end   INTEGER,
    awarded_at   INTEGER NOT NULL
);
