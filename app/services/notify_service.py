"""Notification adapter (spec §15.2).

Exposes ``notify()`` and ``speak()``. Backends: ntfy, no-op. HA is a future
implementation. Failures are logged, never surfaced to the kiosk, never retried
into the critical path.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Config

log = logging.getLogger("routine_runner.notify")


def notify(config: Config, title: str, message: str, priority: str = "default") -> None:
    if config.notify_backend == "ntfy" and config.ntfy_topic:
        try:
            httpx.post(
                f"https://ntfy.sh/{config.ntfy_topic}",
                data=message.encode(),
                headers={"Title": title, "Priority": priority},
                timeout=5.0,
            )
        except Exception:  # noqa: BLE001 - never break the routine on notify failure
            log.warning("ntfy notify failed", exc_info=True)
    else:
        log.info("notify (noop): %s — %s", title, message)


def speak(config: Config, message: str) -> None:
    # Audio is out of scope for v1 (spec §2); no-op until HA integration (§15).
    log.info("speak (noop): %s", message)
