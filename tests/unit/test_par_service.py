"""Par engine over multiple runs: bootstrap ramp, snapshot, weekly recompute."""

from __future__ import annotations

import pytest

from app.db.instance_db import instance_conn
from app.services import clock, par_service, run_service


class FakeClock:
    def __init__(self, start=1_000_000):
        self.t = start

    def advance(self, seconds):
        self.t += seconds * 1000

    def now_ms(self):
        return self.t


@pytest.fixture
def fake_clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr(clock, "now_ms", fc.now_ms)
    return fc


def _run_once(conn, routine_id, child_id, per_step_seconds, fc):
    """Run a task-only routine, spending a fixed number of seconds on each step."""
    run = run_service.open_run(conn, routine_id, "p")
    seg = run_service.check_in(conn, run.id, child_id)
    cur = seg
    while cur is not None:
        fc.advance(per_step_seconds)
        cur = run_service.complete_segment(conn, run.id, child_id, cur.id)
    return run.id


def test_run1_has_no_par_then_ramp_sets_best(seeded, bedtime_routine_id, children, fake_clock):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        step_ids = [
            r["step_id"]
            for r in conn.execute(
                "SELECT id AS step_id FROM steps WHERE routine_id=? AND kind='task' "
                "ORDER BY position",
                (bedtime_routine_id,),
            ).fetchall()
        ]
        # run 1: measurement run, no par snapshotted
        run1 = _run_once(conn, bedtime_routine_id, child, 100, fake_clock)
        seg = conn.execute(
            "SELECT par_seconds FROM segments WHERE run_id=? AND child_id=? AND kind='task' "
            "LIMIT 1",
            (run1, child),
        ).fetchone()
        assert seg["par_seconds"] is None  # run 1 has no par (§5.2)

        # after run 1 closed, a 'best' par exists for each step
        par = par_service.live_par(conn, child, step_ids[0])
        assert par is not None
        assert par["basis"] == "best"
        assert par["par_seconds"] == 100 + 30  # best 100 + grace floor 30


def test_par_snapshotted_into_run2(seeded, bedtime_routine_id, children, fake_clock):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        _run_once(conn, bedtime_routine_id, child, 100, fake_clock)
        # run 2: pars now exist and should be snapshotted at check-in
        run2 = run_service.open_run(conn, bedtime_routine_id, "p")
        run_service.check_in(conn, run2.id, child)
        seg = conn.execute(
            "SELECT par_seconds FROM segments WHERE run_id=? AND kind='task' LIMIT 1",
            (run2.id,),
        ).fetchone()
        assert seg["par_seconds"] == 130


def test_basis_switches_to_median_by_run5(seeded, bedtime_routine_id, children, fake_clock):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        step0 = conn.execute(
            "SELECT id FROM steps WHERE routine_id=? AND kind='task' ORDER BY position LIMIT 1",
            (bedtime_routine_id,),
        ).fetchone()["id"]
        for _ in range(4):
            _run_once(conn, bedtime_routine_id, child, 100, fake_clock)
        # 4 completed samples -> next basis is median (run 5+)
        par = par_service.live_par(conn, child, step0)
        assert par["basis"] == "median"


def test_rejected_redo_excluded_from_samples(seeded, morning_routine_id, children, fake_clock):
    """A rejected-then-redone segment must not enter the par sample (§5.4/§6.4)."""
    from app.services import verification_service

    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        brush = conn.execute(
            "SELECT id FROM steps WHERE routine_id=? AND title='Brush teeth'",
            (morning_routine_id,),
        ).fetchone()["id"]
        run = run_service.open_run(conn, morning_routine_id, "p")
        cur = run_service.check_in(conn, run.id, child)
        # Get dressed
        fake_clock.advance(50)
        cur = run_service.complete_segment(conn, run.id, child, cur.id)
        # Brush teeth
        fake_clock.advance(50)
        gate = run_service.complete_segment(conn, run.id, child, cur.id)
        assert gate.state == "gate_open"
        # reject -> back to brush teeth
        fake_clock.advance(40)
        verification_service.reject(conn, gate.id, None, "parent")
        samples = par_service.step_samples(conn, child, brush)
        assert samples == []  # rejected segment not yet done and will be excluded
