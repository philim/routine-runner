"""Run and segment lifecycle — the core state machine (spec §6, technical plan §5.1).

Timing is tracked per *attempt* (``segment_attempts``): a task segment's
``elapsed_seconds`` is the SUM of its attempt durations, not wall-clock end minus
start. This is what makes a gate pause (§6.4, clock stops) and a reject-resume
(§6.4, second attempt accumulates) score correctly — the paused interval between
attempts is never counted.

The no-untimed-gap invariant (§5.1) still holds: a task segment's first attempt
starts exactly when the previous segment ended.
"""

from __future__ import annotations

import json
from sqlite3 import Connection

from app.models.run import Run, RunChild
from app.models.segment import Segment
from app.services import clock, par_service, routine_service, scoring_service
from app.services.ids import new_id


class RunError(Exception):
    pass


# --- events -----------------------------------------------------------------

def _record_event(
    conn: Connection, run_id: str, child_id: str | None, type_: str, source: str, **payload
) -> None:
    conn.execute(
        "INSERT INTO events (id, run_id, child_id, type, payload_json, occurred_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), run_id, child_id, type_, json.dumps(payload), clock.now_ms(), source),
    )


# --- queries ----------------------------------------------------------------

def get_run(conn: Connection, run_id: str) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return Run.from_row(row) if row else None


def active_run(conn: Connection) -> Run | None:
    """The single open/active/paused run, if any (v1 runs one routine at a time)."""
    row = conn.execute(
        "SELECT * FROM runs WHERE state IN ('open', 'active', 'paused') "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return Run.from_row(row) if row else None


def run_children(conn: Connection, run_id: str) -> list[RunChild]:
    rows = conn.execute(
        "SELECT * FROM run_children WHERE run_id = ?", (run_id,)
    ).fetchall()
    return [RunChild.from_row(r) for r in rows]


def child_segments(conn: Connection, run_id: str, child_id: str) -> list[Segment]:
    rows = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? ORDER BY position",
        (run_id, child_id),
    ).fetchall()
    return [Segment.from_row(r) for r in rows]


def current_segment(conn: Connection, run_id: str, child_id: str) -> Segment | None:
    row = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? "
        "AND state IN ('active', 'gate_open') ORDER BY position LIMIT 1",
        (run_id, child_id),
    ).fetchone()
    return Segment.from_row(row) if row else None


def _get_segment(conn: Connection, segment_id: str) -> Segment | None:
    row = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
    return Segment.from_row(row) if row else None


# --- attempt helpers --------------------------------------------------------

def _open_attempt(conn: Connection, segment_id: str, at: int) -> None:
    row = conn.execute(
        "SELECT COALESCE(MAX(attempt_no), 0) AS n FROM segment_attempts WHERE segment_id = ?",
        (segment_id,),
    ).fetchone()
    attempt_no = row["n"] + 1
    conn.execute(
        "INSERT INTO segment_attempts (id, segment_id, attempt_no, started_at) "
        "VALUES (?, ?, ?, ?)",
        (new_id(), segment_id, attempt_no, at),
    )
    conn.execute(
        "UPDATE segments SET attempt_count = ? WHERE id = ?", (attempt_no, segment_id)
    )


def _close_attempt(conn: Connection, segment_id: str, at: int) -> int:
    """Close the segment's open attempt and return the segment's total elapsed seconds."""
    open_attempt = conn.execute(
        "SELECT * FROM segment_attempts WHERE segment_id = ? AND ended_at IS NULL "
        "ORDER BY attempt_no DESC LIMIT 1",
        (segment_id,),
    ).fetchone()
    if open_attempt is not None:
        elapsed = max(0, round((at - open_attempt["started_at"]) / 1000))
        conn.execute(
            "UPDATE segment_attempts SET ended_at = ?, elapsed_seconds = ? WHERE id = ?",
            (at, elapsed, open_attempt["id"]),
        )
    total = conn.execute(
        "SELECT COALESCE(SUM(elapsed_seconds), 0) AS t FROM segment_attempts "
        "WHERE segment_id = ?",
        (segment_id,),
    ).fetchone()["t"]
    return total


def _activate_task(conn: Connection, segment_id: str, at: int) -> None:
    """Open a task segment: set active, stamp first_started_at once, open an attempt."""
    seg = _get_segment(conn, segment_id)
    first_started = seg.first_started_at if seg and seg.first_started_at else at
    conn.execute(
        "UPDATE segments SET state = 'active', first_started_at = ? WHERE id = ?",
        (first_started, segment_id),
    )
    _open_attempt(conn, segment_id, at)


def _open_gate(conn: Connection, segment_id: str, at: int) -> None:
    """Open a gate: the child's clock stops entirely (§6.4). first_started_at marks open time."""
    conn.execute(
        "UPDATE segments SET state = 'gate_open', first_started_at = ? WHERE id = ?",
        (at, segment_id),
    )


def _next_pending(conn: Connection, run_id: str, child_id: str, after_pos: int):
    return conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? AND position > ? "
        "AND state = 'pending' ORDER BY position LIMIT 1",
        (run_id, child_id, after_pos),
    ).fetchone()


def _advance_to_next(conn: Connection, run_id: str, child_id: str, from_pos: int, at: int):
    """Open the next pending segment (task or gate). Returns it, or None if track done."""
    nxt = _next_pending(conn, run_id, child_id, from_pos)
    if nxt is None:
        _complete_track(conn, run_id, child_id)
        _maybe_close_run(conn, run_id)
        return None
    if nxt["kind"] == "gate":
        _open_gate(conn, nxt["id"], at)
        _record_event(conn, run_id, child_id, "gate_opened", "system", segment_id=nxt["id"])
    else:
        _activate_task(conn, nxt["id"], at)
    return _get_segment(conn, nxt["id"])


# --- lifecycle --------------------------------------------------------------

def open_run(conn: Connection, routine_id: str, started_by: str | None) -> Run:
    if active_run(conn) is not None:
        raise RunError("a run is already open")
    routine = routine_service.get_routine(conn, routine_id)
    if routine is None:
        raise RunError(f"unknown routine: {routine_id}")
    run_id = new_id()
    now = clock.now_ms()
    conn.execute(
        "INSERT INTO runs (id, routine_id, state, started_by, started_at, paused_seconds) "
        "VALUES (?, ?, 'open', ?, ?, 0)",
        (run_id, routine_id, started_by, now),
    )
    _record_event(conn, run_id, None, "run_opened", "parent", routine_id=routine_id)
    return get_run(conn, run_id)  # type: ignore[return-value]


def check_in(conn: Connection, run_id: str, child_id: str) -> Segment:
    """Register a child, snapshot the step list + pars into segments, start segment 1."""
    run = get_run(conn, run_id)
    if run is None or run.state not in ("open", "active"):
        raise RunError("run not open for check-in")

    existing = conn.execute(
        "SELECT 1 FROM run_children WHERE run_id = ? AND child_id = ?",
        (run_id, child_id),
    ).fetchone()
    if existing:
        raise RunError("child already checked in")

    now = clock.now_ms()
    conn.execute(
        "INSERT INTO run_children (run_id, child_id, state, checked_in_at, stars) "
        "VALUES (?, ?, 'active', ?, 0)",
        (run_id, child_id, now),
    )
    steps = routine_service.ordered_steps(conn, run.routine_id)
    if not steps:
        raise RunError("routine has no steps")
    if steps[0].kind != "task":
        raise RunError("a routine cannot start with a gate")  # spec §4
    for step in steps:
        conn.execute(
            "INSERT INTO segments (id, run_id, child_id, step_id, kind, position, "
            "attempt_count, rejection_count, state, stars) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'pending', 0)",
            (new_id(), run_id, child_id, step.id, step.kind, step.position),
        )
    # snapshot current pars into the task segments (§5.7)
    par_service.snapshot_for_child(conn, run_id, child_id)

    first = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? ORDER BY position LIMIT 1",
        (run_id, child_id),
    ).fetchone()
    _activate_task(conn, first["id"], now)  # first segment starts at check-in (§5.1)
    if run.state == "open":
        conn.execute("UPDATE runs SET state = 'active' WHERE id = ?", (run_id,))
    _record_event(conn, run_id, child_id, "checked_in", "kiosk")
    return _get_segment(conn, first["id"])  # type: ignore[return-value]


def complete_segment(
    conn: Connection, run_id: str, child_id: str, segment_id: str, at_ms: int | None = None
) -> Segment | None:
    """Close the active task segment and open the next step. Returns it, or None if done."""
    seg = _get_segment(conn, segment_id)
    if seg is None or seg.run_id != run_id or seg.child_id != child_id:
        raise RunError("segment not found on this track")
    if seg.state != "active":
        raise RunError(f"segment not active (state={seg.state})")

    ended = at_ms if at_ms is not None else clock.now_ms()
    reconciled = 1 if at_ms is not None else 0
    elapsed = _close_attempt(conn, segment_id, ended)
    stars = scoring_service.segment_stars(elapsed, seg.par_seconds)
    conn.execute(
        "UPDATE segments SET state = 'done', ended_at = ?, elapsed_seconds = ?, "
        "stars = ?, reconciled = ? WHERE id = ?",
        (ended, elapsed, stars, reconciled, segment_id),
    )
    _record_event(
        conn, run_id, child_id, "segment_done", "kiosk",
        segment_id=segment_id, elapsed_seconds=elapsed, stars=stars,
    )
    return _advance_to_next(conn, run_id, child_id, seg.position, ended)


def skip_segment(conn: Connection, segment_id: str) -> Segment | None:
    """Parent skip: close as skipped (0 stars, excluded from par sample), open next (§6.5)."""
    seg = _get_segment(conn, segment_id)
    if seg is None:
        raise RunError("segment not found")
    if seg.state not in ("active", "pending", "gate_open"):
        raise RunError(f"cannot skip segment in state {seg.state}")
    now = clock.now_ms()
    if seg.state == "active":
        _close_attempt(conn, segment_id, now)
    conn.execute(
        "UPDATE segments SET state = 'skipped', ended_at = ?, stars = 0 WHERE id = ?",
        (now, segment_id),
    )
    _record_event(conn, seg.run_id, seg.child_id, "segment_skipped", "parent",
                  segment_id=segment_id)
    return _advance_to_next(conn, seg.run_id, seg.child_id, seg.position, now)


def pause_run(conn: Connection, run_id: str) -> None:
    run = get_run(conn, run_id)
    if run is None or run.state != "active":
        raise RunError("run not active")
    conn.execute("UPDATE runs SET state = 'paused' WHERE id = ?", (run_id,))
    _record_event(conn, run_id, None, "run_paused", "parent")


def resume_run(conn: Connection, run_id: str) -> None:
    run = get_run(conn, run_id)
    if run is None or run.state != "paused":
        raise RunError("run not paused")
    conn.execute("UPDATE runs SET state = 'active' WHERE id = ?", (run_id,))
    _record_event(conn, run_id, None, "run_resumed", "parent")


def close_run(conn: Connection, run_id: str) -> None:
    """End the run. Any open segments close as incomplete (0 stars) (§6.5)."""
    run = get_run(conn, run_id)
    if run is None or run.state in ("closed", "abandoned"):
        raise RunError("run already closed")
    now = clock.now_ms()
    open_segs = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND state IN ('active', 'gate_open', 'pending')",
        (run_id,),
    ).fetchall()
    for s in open_segs:
        if s["state"] == "active":
            _close_attempt(conn, s["id"], now)
        conn.execute(
            "UPDATE segments SET state = 'incomplete', ended_at = ?, stars = 0 WHERE id = ?",
            (now, s["id"]),
        )
    for rc in run_children(conn, run_id):
        if rc.state != "completed":
            _complete_track(conn, run_id, rc.child_id)
    _finalize_run(conn, run_id, "parent")


# --- internals --------------------------------------------------------------

def _complete_track(conn: Connection, run_id: str, child_id: str) -> None:
    now = clock.now_ms()
    base_stars = conn.execute(
        "SELECT COALESCE(SUM(stars), 0) AS s, COALESCE(SUM(elapsed_seconds), 0) AS total "
        "FROM segments WHERE run_id = ? AND child_id = ?",
        (run_id, child_id),
    ).fetchone()
    # records, streaks and run bonuses (spec §7, §6.6)
    bonus = scoring_service.finalize_track(conn, run_id, child_id)
    total_stars = base_stars["s"] + bonus
    conn.execute(
        "UPDATE run_children SET state = 'completed', completed_at = ?, "
        "total_seconds = ?, stars = ? WHERE run_id = ? AND child_id = ?",
        (now, base_stars["total"], total_stars, run_id, child_id),
    )
    _record_event(conn, run_id, child_id, "track_completed", "system",
                  total_seconds=base_stars["total"], stars=total_stars)


def _maybe_close_run(conn: Connection, run_id: str) -> None:
    """Close the run automatically once every checked-in track is completed."""
    rows = run_children(conn, run_id)
    if rows and all(rc.state == "completed" for rc in rows):
        _finalize_run(conn, run_id, "system")


def _finalize_run(conn: Connection, run_id: str, source: str) -> None:
    run = get_run(conn, run_id)
    if run is not None and run.state in ("closed", "abandoned"):
        return
    conn.execute(
        "UPDATE runs SET state = 'closed', closed_at = ? WHERE id = ?",
        (clock.now_ms(), run_id),
    )
    _record_event(conn, run_id, None, "run_closed", source)
    # advance the bootstrap par ramp now that this run's samples exist (§5.2)
    par_service.recompute_ramp_after_run(conn, run_id)
