"""Run and segment lifecycle — the core state machine (spec §6, technical plan §5.1).

Phase 1 scope: task steps only (no gates, no pars). The no-untimed-gap invariant
(§5.1) is enforced structurally — segment N+1's ``first_started_at`` is always
segment N's ``ended_at``, never a fresh clock read.
"""

from __future__ import annotations

import json
from sqlite3 import Connection

from app.models.run import Run, RunChild
from app.models.segment import Segment
from app.services import clock, routine_service, scoring_service
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
    """Register a child on the run, snapshot the step list into segments, start segment 1."""
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
    # snapshot the routine's steps into pending segments for this child
    steps = routine_service.ordered_steps(conn, run.routine_id)
    if not steps:
        raise RunError("routine has no steps")
    for step in steps:
        conn.execute(
            "INSERT INTO segments (id, run_id, child_id, step_id, kind, position, "
            "attempt_count, rejection_count, state, stars) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'pending', 0)",
            (new_id(), run_id, child_id, step.id, step.kind, step.position),
        )
    # activate the first segment; its clock starts at check-in (§5.1)
    first = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? ORDER BY position LIMIT 1",
        (run_id, child_id),
    ).fetchone()
    conn.execute(
        "UPDATE segments SET state = 'active', first_started_at = ?, attempt_count = 1 "
        "WHERE id = ?",
        (now, first["id"]),
    )
    if run.state == "open":
        conn.execute("UPDATE runs SET state = 'active' WHERE id = ?", (run_id,))
    _record_event(conn, run_id, child_id, "checked_in", "kiosk")
    return _get_segment(conn, first["id"])  # type: ignore[return-value]


def complete_segment(
    conn: Connection, run_id: str, child_id: str, segment_id: str, at_ms: int | None = None
) -> Segment | None:
    """Close the active segment and open the next. Returns the next segment or None if done.

    Enforces the no-untimed-gap invariant: the next segment's ``first_started_at``
    is exactly this segment's ``ended_at`` (§5.1).
    """
    seg = _get_segment(conn, segment_id)
    if seg is None or seg.run_id != run_id or seg.child_id != child_id:
        raise RunError("segment not found on this track")
    if seg.state != "active":
        raise RunError(f"segment not active (state={seg.state})")

    ended = at_ms if at_ms is not None else clock.now_ms()
    reconciled = 1 if at_ms is not None else 0
    elapsed = max(0, round((ended - seg.first_started_at) / 1000))
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

    nxt = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? AND position > ? "
        "AND state = 'pending' ORDER BY position LIMIT 1",
        (run_id, child_id, seg.position),
    ).fetchone()
    if nxt is None:
        _complete_track(conn, run_id, child_id)
        _maybe_close_run(conn, run_id)
        return None

    # open next segment with no untimed gap
    conn.execute(
        "UPDATE segments SET state = 'active', first_started_at = ?, attempt_count = 1 "
        "WHERE id = ?",
        (ended, nxt["id"]),
    )
    return _get_segment(conn, nxt["id"])


def skip_segment(conn: Connection, segment_id: str) -> Segment | None:
    """Parent skip: close as skipped (0 stars, excluded from par sample), open next."""
    seg = _get_segment(conn, segment_id)
    if seg is None:
        raise RunError("segment not found")
    if seg.state not in ("active", "pending"):
        raise RunError(f"cannot skip segment in state {seg.state}")
    now = clock.now_ms()
    conn.execute(
        "UPDATE segments SET state = 'skipped', ended_at = ?, stars = 0 WHERE id = ?",
        (now, segment_id),
    )
    _record_event(conn, seg.run_id, seg.child_id, "segment_skipped", "parent",
                  segment_id=segment_id)
    nxt = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? AND position > ? "
        "AND state = 'pending' ORDER BY position LIMIT 1",
        (seg.run_id, seg.child_id, seg.position),
    ).fetchone()
    if nxt is None:
        _complete_track(conn, seg.run_id, seg.child_id)
        _maybe_close_run(conn, seg.run_id)
        return None
    conn.execute(
        "UPDATE segments SET state = 'active', first_started_at = ?, attempt_count = 1 "
        "WHERE id = ?",
        (now, nxt["id"]),
    )
    return _get_segment(conn, nxt["id"])


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
        conn.execute(
            "UPDATE segments SET state = 'incomplete', ended_at = ?, stars = 0 WHERE id = ?",
            (now, s["id"]),
        )
    for rc in run_children(conn, run_id):
        if rc.state != "completed":
            _complete_track(conn, run_id, rc.child_id)
    conn.execute(
        "UPDATE runs SET state = 'closed', closed_at = ? WHERE id = ?", (now, run_id)
    )
    _record_event(conn, run_id, None, "run_closed", "parent")


# --- internals --------------------------------------------------------------

def _complete_track(conn: Connection, run_id: str, child_id: str) -> None:
    now = clock.now_ms()
    agg = conn.execute(
        "SELECT COALESCE(SUM(elapsed_seconds), 0) AS total, COALESCE(SUM(stars), 0) AS stars "
        "FROM segments WHERE run_id = ? AND child_id = ?",
        (run_id, child_id),
    ).fetchone()
    conn.execute(
        "UPDATE run_children SET state = 'completed', completed_at = ?, "
        "total_seconds = ?, stars = ? WHERE run_id = ? AND child_id = ?",
        (now, agg["total"], agg["stars"], run_id, child_id),
    )
    _record_event(conn, run_id, child_id, "track_completed", "system",
                  total_seconds=agg["total"], stars=agg["stars"])


def _maybe_close_run(conn: Connection, run_id: str) -> None:
    """Close the run automatically once every checked-in track is completed."""
    rows = run_children(conn, run_id)
    if rows and all(rc.state == "completed" for rc in rows):
        conn.execute(
            "UPDATE runs SET state = 'closed', closed_at = ? WHERE id = ?",
            (clock.now_ms(), run_id),
        )
        _record_event(conn, run_id, None, "run_closed", "system")
