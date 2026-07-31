"""Routine and step management — read access plus parent configuration (spec §12).

Mid-run edits never affect an active run: runs snapshot their step list into
segments at check-in (``run_service.check_in``), so anything changed here only
takes effect on the *next* run.
"""

from __future__ import annotations

from sqlite3 import Connection

from app.models.routine import Routine
from app.models.step import Step
from app.services.ids import new_id

FLOOR_STEP_SECONDS = 15  # +/- stepper granularity (spec §12)
DEFAULT_FLOOR_SECONDS = 30


class ConfigError(Exception):
    pass


# --- reads --------------------------------------------------------------

def get_routine(conn: Connection, routine_id: str) -> Routine | None:
    row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
    return Routine.from_row(row) if row else None


def list_routines(conn: Connection) -> list[Routine]:
    rows = conn.execute(
        "SELECT * FROM routines WHERE active = 1 ORDER BY name"
    ).fetchall()
    return [Routine.from_row(r) for r in rows]


def list_all_routines(conn: Connection) -> list[Routine]:
    """Every routine including disabled ones, for the parent config list."""
    rows = conn.execute("SELECT * FROM routines ORDER BY active DESC, name").fetchall()
    return [Routine.from_row(r) for r in rows]


def ordered_steps(conn: Connection, routine_id: str) -> list[Step]:
    """Active steps only, in run order — what a new run snapshots."""
    rows = conn.execute(
        "SELECT * FROM steps WHERE routine_id = ? AND active = 1 ORDER BY position",
        (routine_id,),
    ).fetchall()
    return [Step.from_row(r) for r in rows]


def all_steps(conn: Connection, routine_id: str) -> list[Step]:
    """Every step (including disabled) in position order, for the edit page."""
    rows = conn.execute(
        "SELECT * FROM steps WHERE routine_id = ? ORDER BY position",
        (routine_id,),
    ).fetchall()
    return [Step.from_row(r) for r in rows]


def get_step(conn: Connection, step_id: str) -> Step | None:
    row = conn.execute("SELECT * FROM steps WHERE id = ?", (step_id,)).fetchone()
    return Step.from_row(row) if row else None


# --- routines -------------------------------------------------------------

def create_routine(conn: Connection, name: str) -> Routine:
    name = name.strip()
    if not name:
        raise ConfigError("routine name is required")
    routine_id = new_id()
    conn.execute(
        "INSERT INTO routines (id, name, kind, active) VALUES (?, ?, ?, 1)",
        (routine_id, name, name),
    )
    return get_routine(conn, routine_id)  # type: ignore[return-value]


def set_routine_active(conn: Connection, routine_id: str, active: bool) -> None:
    conn.execute(
        "UPDATE routines SET active = ? WHERE id = ?", (1 if active else 0, routine_id)
    )


def set_schedule(conn: Connection, routine_id: str, schedule_cron: str | None) -> None:
    conn.execute(
        "UPDATE routines SET schedule_cron = ? WHERE id = ?",
        (schedule_cron or None, routine_id),
    )


# --- steps ------------------------------------------------------------------

def _next_position(conn: Connection, routine_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS p FROM steps WHERE routine_id = ?",
        (routine_id,),
    ).fetchone()
    return row["p"] + 1


def _validate_gate_target(
    conn: Connection, routine_id: str, gate_position: int, on_reject_step_id: str
) -> None:
    """A gate's on_reject_step_id must be an earlier task in the same routine (§4)."""
    target = get_step(conn, on_reject_step_id)
    if target is None or target.routine_id != routine_id:
        raise ConfigError("on_reject step must belong to the same routine")
    if target.kind != "task":
        raise ConfigError("on_reject step must be a task, not a gate")
    if target.position >= gate_position:
        raise ConfigError("on_reject step must come before the gate")


def add_step(
    conn: Connection,
    routine_id: str,
    title: str,
    icon: str | None,
    kind: str,
    floor_seconds: int | None = None,
    on_reject_step_id: str | None = None,
) -> Step:
    title = title.strip()
    if not title:
        raise ConfigError("step title is required")
    if kind not in ("task", "gate"):
        raise ConfigError(f"invalid step kind: {kind}")

    position = _next_position(conn, routine_id)
    if kind == "gate":
        if position == 0:
            raise ConfigError("a routine cannot start with a gate")
        if not on_reject_step_id:
            raise ConfigError("a gate needs a step to return to on rejection")
        _validate_gate_target(conn, routine_id, position, on_reject_step_id)
        floor_seconds = None
    else:
        on_reject_step_id = None
        if floor_seconds is None:
            floor_seconds = DEFAULT_FLOOR_SECONDS

    step_id = new_id()
    conn.execute(
        "INSERT INTO steps (id, routine_id, position, title, icon, kind, "
        "floor_seconds, on_reject_step_id, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (step_id, routine_id, position, title, icon, kind, floor_seconds, on_reject_step_id),
    )
    return get_step(conn, step_id)  # type: ignore[return-value]


def update_step(
    conn: Connection,
    step_id: str,
    title: str | None = None,
    icon: str | None = None,
    floor_seconds: int | None = None,
    on_reject_step_id: str | None = None,
) -> Step:
    step = get_step(conn, step_id)
    if step is None:
        raise ConfigError("step not found")

    new_title = title.strip() if title is not None else step.title
    if not new_title:
        raise ConfigError("step title is required")
    new_icon = icon if icon is not None else step.icon

    if step.kind == "task":
        new_floor = floor_seconds if floor_seconds is not None else step.floor_seconds
        conn.execute(
            "UPDATE steps SET title = ?, icon = ?, floor_seconds = ? WHERE id = ?",
            (new_title, new_icon, new_floor, step_id),
        )
    else:
        new_target = (
            on_reject_step_id if on_reject_step_id is not None else step.on_reject_step_id
        )
        _validate_gate_target(conn, step.routine_id, step.position, new_target)
        conn.execute(
            "UPDATE steps SET title = ?, icon = ?, on_reject_step_id = ? WHERE id = ?",
            (new_title, new_icon, new_target, step_id),
        )
    return get_step(conn, step_id)  # type: ignore[return-value]


def adjust_floor(conn: Connection, step_id: str, delta_seconds: int) -> Step:
    """+/- stepper for a task's floor time (spec §12), clamped to >= 0."""
    step = get_step(conn, step_id)
    if step is None or step.kind != "task":
        raise ConfigError("floor time only applies to tasks")
    current = step.floor_seconds or 0
    new_floor = max(0, current + delta_seconds)
    conn.execute("UPDATE steps SET floor_seconds = ? WHERE id = ?", (new_floor, step_id))
    return get_step(conn, step_id)  # type: ignore[return-value]


def set_step_active(conn: Connection, step_id: str, active: bool) -> None:
    """Soft disable/enable — preserves history rather than deleting (spec §12)."""
    step = get_step(conn, step_id)
    if step is None:
        raise ConfigError("step not found")
    if active and step.kind == "gate" and step.position == 0:
        raise ConfigError("a routine cannot start with a gate")
    conn.execute("UPDATE steps SET active = ? WHERE id = ?", (1 if active else 0, step_id))


def duplicate_step(conn: Connection, step_id: str) -> Step:
    """Duplicate step (spec §12) — inserted immediately after the original.

    Gates are not duplicable: a copy would need its own on_reject target and
    immediately violate the "earlier task" rule at the same position.
    """
    step = get_step(conn, step_id)
    if step is None:
        raise ConfigError("step not found")
    if step.kind == "gate":
        raise ConfigError("gates cannot be duplicated")
    conn.execute(
        "UPDATE steps SET position = position + 1 WHERE routine_id = ? AND position > ?",
        (step.routine_id, step.position),
    )
    new_step_id = new_id()
    conn.execute(
        "INSERT INTO steps (id, routine_id, position, title, icon, kind, "
        "floor_seconds, on_reject_step_id, active) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1)",
        (new_step_id, step.routine_id, step.position + 1, f"{step.title} (copy)",
         step.icon, step.kind, step.floor_seconds),
    )
    return get_step(conn, new_step_id)  # type: ignore[return-value]


def move_step(conn: Connection, step_id: str, direction: str) -> None:
    """Swap this step's position with its immediate neighbour (spec §12 reorder).

    Blocks a move that would place a gate at position 0 (spec §4).
    """
    if direction not in ("up", "down"):
        raise ConfigError(f"invalid direction: {direction}")
    step = get_step(conn, step_id)
    if step is None:
        raise ConfigError("step not found")

    op = "<" if direction == "up" else ">"
    order = "DESC" if direction == "up" else "ASC"
    neighbour = conn.execute(
        f"SELECT * FROM steps WHERE routine_id = ? AND position {op} ? "
        f"ORDER BY position {order} LIMIT 1",
        (step.routine_id, step.position),
    ).fetchone()
    if neighbour is None:
        return  # already at the edge; no-op

    new_step_pos = neighbour["position"]
    new_neighbour_pos = step.position
    if direction == "up" and step.kind == "gate" and new_step_pos == 0:
        raise ConfigError("a routine cannot start with a gate")
    if direction == "down" and neighbour["kind"] == "gate" and new_neighbour_pos == 0:
        raise ConfigError("a routine cannot start with a gate")

    conn.execute("UPDATE steps SET position = ? WHERE id = ?", (new_step_pos, step.id))
    conn.execute("UPDATE steps SET position = ? WHERE id = ?", (new_neighbour_pos, neighbour["id"]))
