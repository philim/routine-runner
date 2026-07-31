"""Routine/step configuration (spec §12, technical plan Phase 4)."""

from __future__ import annotations

import pytest

from app.db.instance_db import instance_conn
from app.services import routine_service


def test_create_routine_requires_a_name(seeded):
    with instance_conn(seeded) as conn:
        with pytest.raises(routine_service.ConfigError):
            routine_service.create_routine(conn, "   ")


def test_add_task_step_defaults_and_position(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        s1 = routine_service.add_step(conn, routine.id, "Make bed", "🛏️", "task")
        s2 = routine_service.add_step(conn, routine.id, "Brush hair", "💇", "task")
        assert s1.position == 0
        assert s2.position == 1
        assert s1.floor_seconds == routine_service.DEFAULT_FLOOR_SECONDS
        assert s1.on_reject_step_id is None


def test_gate_cannot_be_first_step(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        with pytest.raises(routine_service.ConfigError):
            routine_service.add_step(conn, routine.id, "Check", "🔎", "gate")


def test_gate_requires_earlier_task_target(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task = routine_service.add_step(conn, routine.id, "Make bed", None, "task")
        # no target at all
        with pytest.raises(routine_service.ConfigError):
            routine_service.add_step(conn, routine.id, "Check", "🔎", "gate")
        # target from a different routine
        other = routine_service.create_routine(conn, "other")
        other_task = routine_service.add_step(conn, other.id, "Other task", None, "task")
        with pytest.raises(routine_service.ConfigError):
            routine_service.add_step(
                conn, routine.id, "Check", "🔎", "gate", on_reject_step_id=other_task.id
            )
        # valid target succeeds
        gate = routine_service.add_step(
            conn, routine.id, "Check", "🔎", "gate", on_reject_step_id=task.id
        )
        assert gate.kind == "gate"
        assert gate.floor_seconds is None
        assert gate.on_reject_step_id == task.id


def test_gate_target_must_be_earlier_not_later(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task1 = routine_service.add_step(conn, routine.id, "First", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Check", "🔎", "gate", on_reject_step_id=task1.id
        )
        task_later = routine_service.add_step(conn, routine.id, "Later", None, "task")
        # retargeting the gate to a step that comes AFTER it must be rejected
        with pytest.raises(routine_service.ConfigError):
            routine_service.update_step(conn, gate.id, on_reject_step_id=task_later.id)


def test_move_step_swaps_position(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        s1 = routine_service.add_step(conn, routine.id, "A", None, "task")
        s2 = routine_service.add_step(conn, routine.id, "B", None, "task")
        routine_service.move_step(conn, s2.id, "up")
        steps = routine_service.all_steps(conn, routine.id)
        assert [s.title for s in steps] == ["B", "A"]
        assert steps[0].id == s2.id and steps[0].position == 0
        assert steps[1].id == s1.id and steps[1].position == 1


def test_move_step_at_edge_is_a_noop(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        s1 = routine_service.add_step(conn, routine.id, "A", None, "task")
        routine_service.move_step(conn, s1.id, "up")  # already first
        assert routine_service.get_step(conn, s1.id).position == 0


def test_move_blocks_gate_into_position_zero(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task = routine_service.add_step(conn, routine.id, "Task", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Gate", "🔎", "gate", on_reject_step_id=task.id
        )
        with pytest.raises(routine_service.ConfigError):
            routine_service.move_step(conn, gate.id, "up")
        # positions unchanged
        assert routine_service.get_step(conn, task.id).position == 0
        assert routine_service.get_step(conn, gate.id).position == 1


def test_adjust_floor_clamps_to_zero(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        s = routine_service.add_step(conn, routine.id, "A", None, "task", floor_seconds=10)
        routine_service.adjust_floor(conn, s.id, -100)
        assert routine_service.get_step(conn, s.id).floor_seconds == 0
        routine_service.adjust_floor(conn, s.id, 15)
        assert routine_service.get_step(conn, s.id).floor_seconds == 15


def test_adjust_floor_rejects_gates(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task = routine_service.add_step(conn, routine.id, "Task", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Gate", "🔎", "gate", on_reject_step_id=task.id
        )
        with pytest.raises(routine_service.ConfigError):
            routine_service.adjust_floor(conn, gate.id, 15)


def test_set_step_active_soft_disables(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        s = routine_service.add_step(conn, routine.id, "A", None, "task")
        routine_service.set_step_active(conn, s.id, False)
        assert routine_service.get_step(conn, s.id).active == 0
        # disabled steps are excluded from the run-time ordered list...
        assert routine_service.ordered_steps(conn, routine.id) == []
        # ...but still visible in the full admin list (history preserved)
        assert len(routine_service.all_steps(conn, routine.id)) == 1
        routine_service.set_step_active(conn, s.id, True)
        assert routine_service.get_step(conn, s.id).active == 1


def test_db_check_constraint_blocks_gate_at_position_zero(seeded):
    """Belt and suspenders: even a raw UPDATE can't put a gate at position 0 —
    the DDL's CHECK constraint (spec §4) enforces it independently of the
    service-layer guards in move_step/set_step_active."""
    import sqlite3

    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task = routine_service.add_step(conn, routine.id, "Task", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Gate", "🔎", "gate", on_reject_step_id=task.id
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE steps SET position = 0 WHERE id = ?", (gate.id,))


def test_duplicate_step_inserts_after_original(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        s1 = routine_service.add_step(conn, routine.id, "A", None, "task", floor_seconds=45)
        s2 = routine_service.add_step(conn, routine.id, "B", None, "task")
        copy = routine_service.duplicate_step(conn, s1.id)
        steps = routine_service.all_steps(conn, routine.id)
        assert [s.title for s in steps] == ["A", "A (copy)", "B"]
        assert copy.floor_seconds == 45
        assert routine_service.get_step(conn, s2.id).position == 2


def test_duplicate_gate_is_rejected(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task = routine_service.add_step(conn, routine.id, "Task", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Gate", "🔎", "gate", on_reject_step_id=task.id
        )
        with pytest.raises(routine_service.ConfigError):
            routine_service.duplicate_step(conn, gate.id)


def test_update_step_title_and_gate_target(seeded):
    with instance_conn(seeded) as conn:
        routine = routine_service.create_routine(conn, "weekend")
        task1 = routine_service.add_step(conn, routine.id, "First", None, "task")
        task2 = routine_service.add_step(conn, routine.id, "Second", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Gate", "🔎", "gate", on_reject_step_id=task1.id
        )
        updated = routine_service.update_step(conn, gate.id, title="Renamed gate")
        assert updated.title == "Renamed gate"
        assert updated.on_reject_step_id == task1.id  # unchanged when not passed

        retargeted = routine_service.update_step(
            conn, gate.id, on_reject_step_id=task2.id
        )
        assert retargeted.on_reject_step_id == task2.id
