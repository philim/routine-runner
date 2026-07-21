"""Device enrolment, token minting, and validation (spec §9.2).

Every device — both parent phones and the kiosk — is a row in ``devices``.
Tokens are JWTs whose ``jti`` is checked against the table on every request so
revocation is instant.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Config
from app.db import connect
from app.models.device import Device
from app.services import clock
from app.services.ids import new_id

ALGO = "HS256"
log = logging.getLogger("routine_runner.device_service")

# Auth hits /kiosk/state every few seconds; don't write last_seen on every check.
_LAST_SEEN_MIN_INTERVAL_MS = 30_000


class EnrolmentError(Exception):
    pass


@dataclass
class MintedToken:
    device: Device
    jwt: str


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_enrolment_token(config: Config, role: str, created_by: str | None) -> str:
    """Create a single-use, short-TTL enrolment token; return the raw claim code."""
    if role not in ("parent", "kiosk"):
        raise EnrolmentError(f"invalid role: {role}")
    raw = secrets.token_urlsafe(24)
    expires_at = clock.now_ms() + config.enrol_ttl_seconds * 1000
    with connect(config.main_db_path, immediate=True) as conn:
        conn.execute(
            "INSERT INTO enrolment_tokens "
            "(token_hash, household_id, role, expires_at, consumed_at, created_by) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (_hash(raw), config.household_id, role, expires_at, created_by),
        )
    return raw


def claim(config: Config, raw_token: str, label: str, email: str | None = None) -> MintedToken:
    """Consume an enrolment token, create the device, and mint a JWT."""
    token_hash = _hash(raw_token)
    now = clock.now_ms()
    normalized_email: str | None = None
    if email:
        from app.services import otp_service

        normalized_email = otp_service.normalize_email(email)
        if not otp_service.is_valid_email(normalized_email):
            raise EnrolmentError("invalid email address")

    with connect(config.main_db_path, immediate=True) as conn:
        row = conn.execute(
            "SELECT * FROM enrolment_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            raise EnrolmentError("unknown enrolment token")
        if row["consumed_at"] is not None:
            raise EnrolmentError("enrolment token already used")
        if row["expires_at"] < now:
            raise EnrolmentError("enrolment token expired")
        if normalized_email and row["role"] != "parent":
            raise EnrolmentError("email is only for parent devices")
        if normalized_email:
            taken = conn.execute(
                "SELECT 1 FROM devices "
                "WHERE email = ? AND revoked_at IS NULL LIMIT 1",
                (normalized_email,),
            ).fetchone()
            if taken:
                raise EnrolmentError("email already registered to a parent device")

        device_id = new_id()
        jti = new_id()
        conn.execute(
            "INSERT INTO devices "
            "(id, household_id, label, role, jti, created_at, email) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                row["household_id"],
                label,
                row["role"],
                jti,
                now,
                normalized_email if row["role"] == "parent" else None,
            ),
        )
        conn.execute(
            "UPDATE enrolment_tokens SET consumed_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
        device = Device(
            id=device_id,
            household_id=row["household_id"],
            label=label,
            role=row["role"],
            jti=jti,
            created_at=now,
            email=normalized_email if row["role"] == "parent" else None,
        )
    return MintedToken(device=device, jwt=_encode(config, device))


def mint_session(config: Config, device: Device) -> str:
    """Issue a JWT for an existing live device (e.g. after OTP login)."""
    return _encode(config, device)


DEV_PARENT_LABEL = "Dev Parent"


def get_or_create_dev_parent(config: Config) -> Device:
    """Return a live parent device for dev auth bypass, creating one if needed.

    DANGER: only call from dev-mode-gated code paths — this hands out a parent
    session without any credential check.
    """
    if not config.dev_mode:
        raise EnrolmentError("dev login is disabled")
    with connect(config.main_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM devices "
            "WHERE role = 'parent' AND revoked_at IS NULL "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is not None:
            return Device.from_row(row)
    raw = create_enrolment_token(config, role="parent", created_by="dev")
    return claim(config, raw, DEV_PARENT_LABEL).device


def set_email(config: Config, device_id: str, email: str | None) -> Device:
    """Set or clear the login email on a parent device."""
    from app.services import otp_service

    normalized: str | None = None
    if email:
        normalized = otp_service.normalize_email(email)
        if not otp_service.is_valid_email(normalized):
            raise EnrolmentError("invalid email address")

    with connect(config.main_db_path, immediate=True) as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE id = ? AND revoked_at IS NULL",
            (device_id,),
        ).fetchone()
        if row is None:
            raise EnrolmentError("device not found")
        if row["role"] != "parent":
            raise EnrolmentError("email is only for parent devices")
        if normalized:
            taken = conn.execute(
                "SELECT 1 FROM devices "
                "WHERE email = ? AND revoked_at IS NULL AND id != ? LIMIT 1",
                (normalized, device_id),
            ).fetchone()
            if taken:
                raise EnrolmentError("email already registered to a parent device")
        conn.execute(
            "UPDATE devices SET email = ? WHERE id = ?",
            (normalized, device_id),
        )
        updated = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return Device.from_row(updated)


def _encode(config: Config, device: Device) -> str:
    exp = datetime.now(UTC) + timedelta(days=config.token_ttl_days)
    payload = {
        "sub": device.id,
        "role": device.role,
        "jti": device.jti,
        "exp": exp,
    }
    return jwt.encode(payload, config.jwt_secret, algorithm=ALGO)


def validate(config: Config, token: str) -> Device | None:
    """Decode a JWT and confirm its jti maps to a live (non-revoked) device.

    The lookup is read-only so concurrent kiosk/SSE auth checks do not fight
    over a write lock. ``last_seen_at`` is touched best-effort and throttled.
    """
    try:
        payload = jwt.decode(token, config.jwt_secret, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
    with connect(config.main_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE id = ? AND jti = ? AND revoked_at IS NULL",
            (payload.get("sub"), payload.get("jti")),
        ).fetchone()
        if row is None:
            return None
        device = Device.from_row(row)
        last = row["last_seen_at"]
        now = clock.now_ms()
        should_touch = last is None or (now - last) >= _LAST_SEEN_MIN_INTERVAL_MS
    if should_touch:
        _touch_last_seen(config, device.id, now)
    return device


def _touch_last_seen(config: Config, device_id: str, now: int) -> None:
    """Best-effort presence stamp; never fails auth on lock contention."""
    try:
        with connect(config.main_db_path, immediate=True) as conn:
            conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE id = ?",
                (now, device_id),
            )
    except sqlite3.OperationalError:
        log.warning("last_seen_at update skipped for %s (database locked)", device_id)


def list_devices(config: Config) -> list[Device]:
    with connect(config.main_db_path) as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [Device.from_row(r) for r in rows]


def revoke(config: Config, device_id: str) -> None:
    with connect(config.main_db_path, immediate=True) as conn:
        conn.execute(
            "UPDATE devices SET revoked_at = ? WHERE id = ?",
            (clock.now_ms(), device_id),
        )
