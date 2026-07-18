"""Single authoritative time source.

Every server-stamped timestamp flows through this module so tests can freeze
time in one place (§5.7 of the design spec: the server is authoritative). Time
is stored and passed around as epoch milliseconds (integers) per the technical
plan §4.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now_ms() -> int:
    """Current time as epoch milliseconds (UTC)."""
    return int(datetime.now(UTC).timestamp() * 1000)


def to_iso(ms: int | None) -> str | None:
    """Render epoch-ms as an ISO-8601 UTC string for display/audit."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()
