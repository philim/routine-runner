"""Gate verification: approve, reject-with-resume, auto-approve (spec §6.4, §6.4.1).

A gate is a blocking step. While it is open the child's clock is stopped. On
approve the next step opens; on reject the child returns to an earlier task whose
clock resumes (the second attempt accumulates onto the first, scored once against
the same par). A 5-minute backstop auto-approves so a child is never stranded.
"""

from __future__ import annotations

from sqlite3 import Connection

from app.services import clock, run_service
from app.services.run_service import RunError, _advance_to_next, _get_segment, _record_event

NUDGE_INTERVAL_MS = 30_000
AUTO_APPROVE_SECONDS = 300


def open_gates(conn: Connection) -> list[dict]:
    """All gates awaiting a parent decision, with child + step context (spec §11)."""
    rows = conn.execute(
        "SELECT s.*, c.name AS child_name, st.title AS step_title "
        "FROM segments s JOIN children c ON c.id = s.child_id "
        "JOIN steps st ON st.id = s.step_id "
        "WHERE s.state = 'gate_open' ORDER BY s.first_started_at",
    ).fetchall()
    return [dict(r) for r in rows]


def _accrue_wait(conn: Connection, gate: object, now: int) -> None:
    prior = gate.gate_wait_seconds or 0
    this_cycle = max(0, round((now - gate.first_started_at) / 1000)) if gate.first_started_at else 0
    conn.execute(
        "UPDATE segments SET gate_wait_seconds = ? WHERE id = ?",
        (prior + this_cycle, gate.id),
    )


def approve(
    conn: Connection, segment_id: str, quality_stars: int, note: str | None, reviewed_by: str
):
    """Approve a gate; the next step opens with the clock starting now (§6.4)."""
    gate = _get_segment(conn, segment_id)
    if gate is None or gate.kind != "gate" or gate.state != "gate_open":
        raise RunError("gate not open")
    now = clock.now_ms()
    _accrue_wait(conn, gate, now)
    conn.execute(
        "UPDATE segments SET state = 'done', resolution = 'approved', quality_stars = ?, "
        "reviewed_by = ?, reviewed_at = ?, note = ?, ended_at = ? WHERE id = ?",
        (max(0, min(3, quality_stars)), reviewed_by, now, note, now, segment_id),
    )
    _record_event(conn, gate.run_id, gate.child_id, "gate_approved", "parent",
                  segment_id=segment_id, quality_stars=quality_stars)
    return _advance_to_next(conn, gate.run_id, gate.child_id, gate.position, now)


def auto_approve(conn: Connection, segment_id: str):
    """Backstop auto-approval at timeout: 0 quality stars, logged (§6.4.1)."""
    gate = _get_segment(conn, segment_id)
    if gate is None or gate.state != "gate_open":
        return None  # already resolved
    now = clock.now_ms()
    _accrue_wait(conn, gate, now)
    conn.execute(
        "UPDATE segments SET state = 'done', resolution = 'auto_approved', quality_stars = 0, "
        "reviewed_at = ?, ended_at = ? WHERE id = ?",
        (now, now, segment_id),
    )
    _record_event(conn, gate.run_id, gate.child_id, "gate_auto_approved", "system",
                  segment_id=segment_id)
    return _advance_to_next(conn, gate.run_id, gate.child_id, gate.position, now)


def reject(conn: Connection, segment_id: str, note: str | None, reviewed_by: str):
    """Reject a gate; the child returns to on_reject_step_id, clock resumes (§6.4)."""
    gate = _get_segment(conn, segment_id)
    if gate is None or gate.kind != "gate" or gate.state != "gate_open":
        raise RunError("gate not open")
    step = conn.execute(
        "SELECT on_reject_step_id FROM steps WHERE id = ?", (gate.step_id,)
    ).fetchone()
    target_step_id = step["on_reject_step_id"] if step else None
    if target_step_id is None:
        raise RunError("gate has no on_reject_step_id")

    target = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? AND step_id = ?",
        (gate.run_id, gate.child_id, target_step_id),
    ).fetchone()
    if target is None or target["position"] >= gate.position:
        raise RunError("on_reject_step_id must point to an earlier step")

    now = clock.now_ms()
    _accrue_wait(conn, gate, now)
    conn.execute(
        "UPDATE segments SET rejection_count = rejection_count + 1, note = ?, "
        "reviewed_by = ? WHERE id = ?",
        (note, reviewed_by, segment_id),
    )
    # reset the gate and any segments between the target and the gate back to pending,
    # so the child re-walks them; the gate will reopen when reached again.
    conn.execute(
        "UPDATE segments SET state = 'pending', ended_at = NULL "
        "WHERE run_id = ? AND child_id = ? AND position > ? AND position <= ?",
        (gate.run_id, gate.child_id, target["position"], gate.position),
    )
    # reopen the target task: resume its clock via a fresh attempt (accumulates)
    conn.execute(
        "UPDATE segments SET state = 'active', ended_at = NULL, stars = 0, "
        "rejection_count = rejection_count + 1 WHERE id = ?",
        (target["id"],),
    )
    run_service._open_attempt(conn, target["id"], now)
    _record_event(conn, gate.run_id, gate.child_id, "gate_rejected", "parent",
                  segment_id=segment_id, target_step_id=target_step_id)
    return _get_segment(conn, target["id"])


def can_nudge(conn: Connection, segment_id: str) -> bool:
    """Rate-limit the child's 'Ask again' to once per 30s (§6.4.1)."""
    row = conn.execute(
        "SELECT MAX(occurred_at) AS last FROM events "
        "WHERE type = 'gate_nudge' AND payload_json LIKE ?",
        (f'%{segment_id}%',),
    ).fetchone()
    if row is None or row["last"] is None:
        return True
    return clock.now_ms() - row["last"] >= NUDGE_INTERVAL_MS


def record_nudge(conn: Connection, segment_id: str) -> None:
    gate = _get_segment(conn, segment_id)
    if gate is not None:
        _record_event(conn, gate.run_id, gate.child_id, "gate_nudge", "kiosk",
                      segment_id=segment_id)
