"""UUID id generation, isolated so tests can monkeypatch if needed."""

from __future__ import annotations

import uuid


def new_id() -> str:
    return uuid.uuid4().hex
