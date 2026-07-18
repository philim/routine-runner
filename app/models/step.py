from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class Step:
    id: str
    routine_id: str
    position: int
    title: str
    icon: str | None
    kind: str  # task | gate
    floor_seconds: int | None
    on_reject_step_id: str | None
    active: int

    @classmethod
    def from_row(cls, row: Row) -> Step:
        return cls(
            id=row["id"],
            routine_id=row["routine_id"],
            position=row["position"],
            title=row["title"],
            icon=row["icon"],
            kind=row["kind"],
            floor_seconds=row["floor_seconds"],
            on_reject_step_id=row["on_reject_step_id"],
            active=row["active"],
        )
