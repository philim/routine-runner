from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class Routine:
    id: str
    name: str
    kind: str
    schedule_cron: str | None
    active: int

    @classmethod
    def from_row(cls, row: Row) -> Routine:
        return cls(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            schedule_cron=row["schedule_cron"],
            active=row["active"],
        )
