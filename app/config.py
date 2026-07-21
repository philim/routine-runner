"""Application configuration, sourced from environment with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}

# Fallback signing key for local dev only; unsafe for production use.
INSECURE_JWT_SECRET = "dev-insecure-change-me"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _env_str(name: str, default: str) -> str:
    """Return the env var, falling back to default when unset or blank.

    A blank line in .env (e.g. ``RR_JWT_SECRET=``) must not override the default
    with an empty string — that would produce invalid config (empty HMAC key).
    """
    return os.environ.get(name, "").strip() or default


def _env_opt(name: str) -> str | None:
    """Return a stripped env var, or None when unset or blank."""
    return os.environ.get(name, "").strip() or None


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Config:
    data_dir: Path
    main_db_path: Path
    household_id: str
    household_name: str
    jwt_secret: str
    token_ttl_days: int
    enrol_ttl_seconds: int
    ntfy_topic: str | None
    notify_backend: str  # ntfy | noop
    hostname: str | None  # public TLS hostname (RR_HOSTNAME); used in claim QR URLs
    resend_api_key: str | None
    resend_from: str
    email_backend: str  # resend | noop
    otp_ttl_seconds: int
    otp_resend_seconds: int
    dev_mode: bool = False  # DANGER: enables /parent/dev-login auth bypass

    @property
    def instance_db_path(self) -> Path:
        return self.data_dir / f"household_{self.household_id}.db"


def load_config() -> Config:
    data_dir = Path(_env_str("RR_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    resend_api_key = _env_opt("RESEND_API_KEY") or _env_opt("RR_RESEND_API_KEY")
    email_backend = _env_opt("RR_EMAIL_BACKEND")
    if not email_backend:
        email_backend = "resend" if resend_api_key else "noop"
    return Config(
        data_dir=data_dir,
        main_db_path=data_dir / "main.db",
        household_id=_env_str("RR_HOUSEHOLD_ID", "home"),
        household_name=_env_str("RR_HOUSEHOLD_NAME", "Home"),
        jwt_secret=_env_str("RR_JWT_SECRET", INSECURE_JWT_SECRET),
        token_ttl_days=_env_int("RR_TOKEN_TTL_DAYS", 365),
        enrol_ttl_seconds=_env_int("RR_ENROL_TTL_SECONDS", 300),
        ntfy_topic=_env_opt("RR_NTFY_TOPIC"),
        notify_backend=_env_str("RR_NOTIFY_BACKEND", "noop"),
        hostname=_env_opt("RR_HOSTNAME"),
        resend_api_key=resend_api_key,
        resend_from=_env_str(
            "RR_RESEND_FROM", "Routine Runner <onboarding@resend.dev>"
        ),
        email_backend=email_backend,
        otp_ttl_seconds=_env_int("RR_OTP_TTL_SECONDS", 600),
        otp_resend_seconds=_env_int("RR_OTP_RESEND_SECONDS", 60),
        dev_mode=_env_flag("RR_DEV_MODE"),
    )
