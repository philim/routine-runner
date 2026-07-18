from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class Device:
    id: str
    household_id: str
    label: str
    role: str  # parent | kiosk
    jti: str
    created_at: int
    last_seen_at: int | None = None
    revoked_at: int | None = None

    @classmethod
    def from_row(cls, row: Row) -> Device:
        return cls(
            id=row["id"],
            household_id=row["household_id"],
            label=row["label"],
            role=row["role"],
            jti=row["jti"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )
