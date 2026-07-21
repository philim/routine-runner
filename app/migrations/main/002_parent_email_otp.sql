-- Parent email login: store email on devices and short-lived OTPs.

ALTER TABLE devices ADD COLUMN email TEXT;

-- One active parent per email address.
CREATE UNIQUE INDEX IF NOT EXISTS devices_email_active
    ON devices(email)
    WHERE email IS NOT NULL AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS parent_otps (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    device_id   TEXT NOT NULL REFERENCES devices(id),
    expires_at  INTEGER NOT NULL,
    consumed_at INTEGER,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS parent_otps_email_created
    ON parent_otps(email, created_at DESC);
