"""Outbound email via Resend (or a no-op outbox for tests/dev)."""

from __future__ import annotations

import logging

import httpx

from app.config import Config

log = logging.getLogger("routine_runner.email")

# Captured messages when email_backend=noop (tests / local without API key).
_outbox: list[dict[str, str]] = []


def clear_outbox() -> None:
    _outbox.clear()


def outbox() -> list[dict[str, str]]:
    return list(_outbox)


def send_email(config: Config, *, to: str, subject: str, text: str, html: str) -> None:
    """Send an email. Failures are logged; callers decide whether to surface them."""
    if config.email_backend == "resend":
        if not config.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is required when RR_EMAIL_BACKEND=resend")
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": config.resend_from,
                "to": [to],
                "subject": subject,
                "text": text,
                "html": html,
            },
            timeout=10.0,
        )
        if response.status_code >= 400:
            log.warning(
                "Resend send failed status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()
        return

    _outbox.append({"to": to, "subject": subject, "text": text, "html": html})
    log.info("email (noop) to=%s subject=%s", to, subject)
