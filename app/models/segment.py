from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class Segment:
    id: str
    run_id: str
    child_id: str
    step_id: str
    kind: str  # task | gate
    position: int
    first_started_at: int | None
    ended_at: int | None
    elapsed_seconds: int | None
    gate_wait_seconds: int | None
    par_seconds: int | None
    attempt_count: int
    rejection_count: int
    state: str  # pending | active | gate_open | done | skipped | incomplete
    stars: int
    quality_stars: int | None
    resolution: str | None
    reviewed_by: str | None
    reviewed_at: int | None
    note: str | None
    reconciled: int

    @classmethod
    def from_row(cls, row: Row) -> Segment:
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            child_id=row["child_id"],
            step_id=row["step_id"],
            kind=row["kind"],
            position=row["position"],
            first_started_at=row["first_started_at"],
            ended_at=row["ended_at"],
            elapsed_seconds=row["elapsed_seconds"],
            gate_wait_seconds=row["gate_wait_seconds"],
            par_seconds=row["par_seconds"],
            attempt_count=row["attempt_count"],
            rejection_count=row["rejection_count"],
            state=row["state"],
            stars=row["stars"],
            quality_stars=row["quality_stars"],
            resolution=row["resolution"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
            note=row["note"],
            reconciled=row["reconciled"],
        )
