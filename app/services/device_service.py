"""Device enrolment, token minting, and validation (spec §9.2).

Every device — both parent phones and the kiosk — is a row in ``devices``.
Tokens are JWTs whose ``jti`` is checked against the table on every request so
revocation is instant.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Config
from app.db import connect
from app.models.device import Device
from app.services import clock
from app.services.ids import new_id

ALGO = "HS256"


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
    with connect(config.main_db_path) as conn:
        conn.execute(
            "INSERT INTO enrolment_tokens "
            "(token_hash, household_id, role, expires_at, consumed_at, created_by) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (_hash(raw), config.household_id, role, expires_at, created_by),
        )
    return raw


def claim(config: Config, raw_token: str, label: str) -> MintedToken:
    """Consume an enrolment token, create the device, and mint a JWT."""
    token_hash = _hash(raw_token)
    now = clock.now_ms()
    with connect(config.main_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM enrolment_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            raise EnrolmentError("unknown enrolment token")
        if row["consumed_at"] is not None:
            raise EnrolmentError("enrolment token already used")
        if row["expires_at"] < now:
            raise EnrolmentError("enrolment token expired")

        device_id = new_id()
        jti = new_id()
        conn.execute(
            "INSERT INTO devices (id, household_id, label, role, jti, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, row["household_id"], label, row["role"], jti, now),
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
        )
    return MintedToken(device=device, jwt=_encode(config, device))


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
    """Decode a JWT and confirm its jti maps to a live (non-revoked) device."""
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
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?",
            (clock.now_ms(), row["id"]),
        )
        return Device.from_row(row)


def list_devices(config: Config) -> list[Device]:
    with connect(config.main_db_path) as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [Device.from_row(r) for r in rows]


def revoke(config: Config, device_id: str) -> None:
    with connect(config.main_db_path) as conn:
        conn.execute(
            "UPDATE devices SET revoked_at = ? WHERE id = ?",
            (clock.now_ms(), device_id),
        )
