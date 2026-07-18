-- main.db: households, devices, enrolment tokens, instance registry (spec §10)

CREATE TABLE IF NOT EXISTS households (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id           TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id),
    label        TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('parent', 'kiosk')),
    jti          TEXT NOT NULL UNIQUE,
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER,
    revoked_at   INTEGER
);

CREATE TABLE IF NOT EXISTS enrolment_tokens (
    token_hash   TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id),
    role         TEXT NOT NULL CHECK (role IN ('parent', 'kiosk')),
    expires_at   INTEGER NOT NULL,
    consumed_at  INTEGER,
    created_by   TEXT
);

CREATE TABLE IF NOT EXISTS instances (
    household_id   TEXT PRIMARY KEY REFERENCES households(id),
    db_path        TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 0
);
