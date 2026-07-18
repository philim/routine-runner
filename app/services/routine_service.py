"""Read access to routines and their ordered steps."""

from __future__ import annotations

from sqlite3 import Connection

from app.models.routine import Routine
from app.models.step import Step


def get_routine(conn: Connection, routine_id: str) -> Routine | None:
    row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
    return Routine.from_row(row) if row else None


def list_routines(conn: Connection) -> list[Routine]:
    rows = conn.execute(
        "SELECT * FROM routines WHERE active = 1 ORDER BY name"
    ).fetchall()
    return [Routine.from_row(r) for r in rows]


def ordered_steps(conn: Connection, routine_id: str) -> list[Step]:
    rows = conn.execute(
        "SELECT * FROM steps WHERE routine_id = ? AND active = 1 ORDER BY position",
        (routine_id,),
    ).fetchall()
    return [Step.from_row(r) for r in rows]
