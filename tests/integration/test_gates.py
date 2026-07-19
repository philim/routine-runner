"""Gate behaviour (spec §6.4, §6.4.1): reject-resume scoring, exclusion, auto-approve."""

from __future__ import annotations

import pytest

from app.db.instance_db import instance_conn
from app.services import clock, run_service, verification_service


class FakeClock:
    def __init__(self, start=2_000_000):
        self.t = start

    def advance(self, seconds):
        self.t += seconds * 1000

    def now_ms(self):
        return self.t


@pytest.fixture
def fc(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(clock, "now_ms", c.now_ms)
    return c


def _to_gate(conn, run_id, child, fc, dressed=50, brush=50):
    """Advance a child to the teeth-check gate. Returns the gate segment."""
    cur = run_service.check_in(conn, run_id, child)
    fc.advance(dressed)
    cur = run_service.complete_segment(conn, run_id, child, cur.id)  # Get dressed
    fc.advance(brush)
    gate = run_service.complete_segment(conn, run_id, child, cur.id)  # Brush teeth -> gate
    return gate


def test_gate_opens_and_stops_clock(seeded, morning_routine_id, children, fc):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        gate = _to_gate(conn, run.id, child, fc)
        assert gate.kind == "gate"
        assert gate.state == "gate_open"


def test_approve_opens_next_and_awards_quality(seeded, morning_routine_id, children, fc):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        gate = _to_gate(conn, run.id, child, fc)
        fc.advance(30)  # parent takes 30s
        nxt = verification_service.approve(conn, gate.id, quality_stars=3, note=None,
                                           reviewed_by="parent")
        assert nxt.state == "active"  # "Hair" opens
        g = conn.execute("SELECT * FROM segments WHERE id=?", (gate.id,)).fetchone()
        assert g["resolution"] == "approved"
        assert g["quality_stars"] == 3
        assert g["gate_wait_seconds"] == 30  # wait recorded, never scored


def test_reject_resumes_clock_and_scores_cumulative(seeded, morning_routine_id, children, fc):
    """Worked example (§6.4): attempt1 50s + attempt2 accumulates vs the same par."""
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        brush = conn.execute(
            "SELECT id FROM steps WHERE routine_id=? AND title='Brush teeth'",
            (morning_routine_id,),
        ).fetchone()["id"]
        # give brush teeth a par so we can check scoring on the cumulative time
        from app.services.ids import new_id
        conn.execute(
            "INSERT INTO pars (id, child_id, step_id, par_seconds, grace_seconds, basis, "
            "effective_from, computed_from_n, frozen) VALUES (?, ?, ?, 120, 30, 'best', 0, 3, 0)",
            (new_id(), child, brush),
        )
        run = run_service.open_run(conn, morning_routine_id, "p")
        gate = _to_gate(conn, run.id, child, fc, brush=50)  # first attempt 50s
        # parent deliberates 40s (clock stopped), then rejects
        fc.advance(40)
        target = verification_service.reject(conn, gate.id, None, "parent")
        assert target.state == "active"
        assert target.step_id == brush
        # second attempt: 80s more -> cumulative 130s
        fc.advance(80)
        after = run_service.complete_segment(conn, run.id, child, target.id)
        seg = conn.execute("SELECT * FROM segments WHERE id=?", (target.id,)).fetchone()
        assert seg["elapsed_seconds"] == 130  # 50 + 80, gate's 40s NOT counted
        assert seg["rejection_count"] >= 1
        # 130 > 120 but <= 1.25*120=150 -> one star (scored once, same par)
        assert seg["stars"] == 1
        # the gate reopened after the redo
        assert after.state == "gate_open"


def test_auto_approve_gives_zero_quality(seeded, morning_routine_id, children, fc):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        gate = _to_gate(conn, run.id, child, fc)
        fc.advance(300)
        nxt = verification_service.auto_approve(conn, gate.id)
        g = conn.execute("SELECT * FROM segments WHERE id=?", (gate.id,)).fetchone()
        assert g["resolution"] == "auto_approved"
        assert g["quality_stars"] == 0
        assert nxt.state == "active"


def test_nudge_rate_limited(seeded, morning_routine_id, children, fc):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        gate = _to_gate(conn, run.id, child, fc)
        assert verification_service.can_nudge(conn, gate.id) is True
        verification_service.record_nudge(conn, gate.id)
        assert verification_service.can_nudge(conn, gate.id) is False  # within 30s
        fc.advance(31)
        assert verification_service.can_nudge(conn, gate.id) is True
