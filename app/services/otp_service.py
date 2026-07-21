"""Parent email OTP login (Resend-backed).

Parents with a configured ``devices.email`` can request a one-time code at
``/parent`` and receive a session cookie for that device without re-scanning QR.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets

from app.config import Config
from app.db import connect
from app.models.device import Device
from app.services import clock, email_service
from app.services.ids import new_id

log = logging.getLogger("routine_runner.otp")

_OTP_LENGTH = 6
_MAX_ATTEMPTS = 5
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OtpError(Exception):
    pass


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    # Cryptographically strong 6-digit numeric code (000000–999999).
    return f"{secrets.randbelow(10**_OTP_LENGTH):0{_OTP_LENGTH}d}"


def find_parent_by_email(config: Config, email: str) -> Device | None:
    email = normalize_email(email)
    with connect(config.main_db_path) as conn:
        row = conn.execute(
            "SELECT * FROM devices "
            "WHERE role = 'parent' AND revoked_at IS NULL AND email = ?",
            (email,),
        ).fetchone()
        return Device.from_row(row) if row else None


def request_otp(config: Config, email: str) -> None:
    """Create and email an OTP when the address matches an active parent.

    Always succeeds from the caller's perspective when the address is well-formed
    so we do not leak whether an email is enrolled.
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise OtpError("Enter a valid email address")

    device = find_parent_by_email(config, email)
    if device is None:
        log.info("otp requested for unknown or unconfigured email")
        return

    now = clock.now_ms()
    with connect(config.main_db_path, immediate=True) as conn:
        recent = conn.execute(
            "SELECT created_at FROM parent_otps "
            "WHERE email = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        if recent and (now - recent["created_at"]) < config.otp_resend_seconds * 1000:
            raise OtpError("Wait a moment before requesting another code")

        code = _generate_code()
        conn.execute(
            "INSERT INTO parent_otps "
            "(id, email, code_hash, device_id, expires_at, consumed_at, attempts, created_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, 0, ?)",
            (
                new_id(),
                email,
                _hash_code(code),
                device.id,
                now + config.otp_ttl_seconds * 1000,
                now,
            ),
        )

    subject = f"Your Routine Runner code: {code}"
    text = (
        f"Your login code is {code}.\n\n"
        f"It expires in {config.otp_ttl_seconds // 60} minutes. "
        "If you did not request this, you can ignore this email."
    )
    html = (
        f"<p>Your login code is <strong style=\"font-size:1.4em;"
        f"letter-spacing:0.15em\">{code}</strong>.</p>"
        f"<p>It expires in {config.otp_ttl_seconds // 60} minutes. "
        "If you did not request this, you can ignore this email.</p>"
    )
    try:
        email_service.send_email(
            config, to=email, subject=subject, text=text, html=html
        )
    except Exception as exc:  # noqa: BLE001 - surface a calm error to the parent
        log.warning("failed to send parent OTP email", exc_info=True)
        raise OtpError("Could not send the email. Try again shortly.") from exc


def verify_otp(config: Config, email: str, code: str) -> Device:
    """Consume a valid OTP and return the matching parent device."""
    email = normalize_email(email)
    code = code.strip().replace(" ", "")
    if not is_valid_email(email):
        raise OtpError("Enter a valid email address")
    if not code.isdigit() or len(code) != _OTP_LENGTH:
        raise OtpError("Enter the 6-digit code from your email")

    now = clock.now_ms()
    with connect(config.main_db_path, immediate=True) as conn:
        row = conn.execute(
            "SELECT * FROM parent_otps "
            "WHERE email = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        if row is None:
            raise OtpError("No active code for that email. Request a new one.")
        if row["expires_at"] < now:
            raise OtpError("That code has expired. Request a new one.")
        if row["attempts"] >= _MAX_ATTEMPTS:
            raise OtpError("Too many attempts. Request a new code.")

        if row["code_hash"] != _hash_code(code):
            conn.execute(
                "UPDATE parent_otps SET attempts = attempts + 1 WHERE id = ?",
                (row["id"],),
            )
            raise OtpError("Incorrect code. Try again.")

        conn.execute(
            "UPDATE parent_otps SET consumed_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        device_row = conn.execute(
            "SELECT * FROM devices WHERE id = ? AND revoked_at IS NULL AND role = 'parent'",
            (row["device_id"],),
        ).fetchone()
        if device_row is None:
            raise OtpError("That parent device is no longer active.")
        return Device.from_row(device_row)
