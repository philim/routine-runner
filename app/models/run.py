from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class Run:
    id: str
    routine_id: str
    state: str  # open | active | paused | closed | abandoned
    started_by: str | None
    started_at: int
    closed_at: int | None
    paused_seconds: int

    @classmethod
    def from_row(cls, row: Row) -> Run:
        return cls(
            id=row["id"],
            routine_id=row["routine_id"],
            state=row["state"],
            started_by=row["started_by"],
            started_at=row["started_at"],
            closed_at=row["closed_at"],
            paused_seconds=row["paused_seconds"],
        )


@dataclass
class RunChild:
    run_id: str
    child_id: str
    state: str  # checked_in | active | completed
    checked_in_at: int | None
    completed_at: int | None
    total_seconds: int | None
    stars: int

    @classmethod
    def from_row(cls, row: Row) -> RunChild:
        return cls(
            run_id=row["run_id"],
            child_id=row["child_id"],
            state=row["state"],
            checked_in_at=row["checked_in_at"],
            completed_at=row["completed_at"],
            total_seconds=row["total_seconds"],
            stars=row["stars"],
        )
