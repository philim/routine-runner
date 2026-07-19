"""Core state-machine tests, including the no-untimed-gap invariant (§5.1)."""

from __future__ import annotations

from app.db.instance_db import instance_conn
from app.services import run_service


def _seg_by_pos(conn, run_id, child_id, pos):
    row = conn.execute(
        "SELECT * FROM segments WHERE run_id=? AND child_id=? AND position=?",
        (run_id, child_id, pos),
    ).fetchone()
    return row


def test_no_untimed_gap_between_segments(seeded, bedtime_routine_id, children):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, bedtime_routine_id, "parent1")
        seg1 = run_service.check_in(conn, run.id, child)
        # complete every segment in order; assert each next starts exactly when prev ended
        cur = seg1
        prev_positions = []
        while cur is not None:
            prev_positions.append(cur.position)
            nxt = run_service.complete_segment(conn, run.id, child, cur.id)
            ended_row = conn.execute(
                "SELECT ended_at FROM segments WHERE id=?", (cur.id,)
            ).fetchone()
            if nxt is not None:
                assert nxt.first_started_at == ended_row["ended_at"], (
                    "next segment must start the instant the previous ended (§5.1)"
                )
            cur = nxt
        # all three bedtime steps were visited
        assert prev_positions == [0, 1, 2]


def test_track_and_run_complete_on_last_done(seeded, bedtime_routine_id, children):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, bedtime_routine_id, "p")
        seg = run_service.check_in(conn, run.id, child)
        cur = seg
        while cur is not None:
            cur = run_service.complete_segment(conn, run.id, child, cur.id)
        rc = run_service.run_children(conn, run.id)[0]
        assert rc.state == "completed"
        # single-child run auto-closes
        assert run_service.get_run(conn, run.id).state == "closed"


def test_participation_stars_awarded_run1(seeded, bedtime_routine_id, children):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, bedtime_routine_id, "p")
        seg = run_service.check_in(conn, run.id, child)
        cur = seg
        while cur is not None:
            cur = run_service.complete_segment(conn, run.id, child, cur.id)
        # one participation star per completed step (segment-level, before bonuses)
        seg_star_sum = conn.execute(
            "SELECT COALESCE(SUM(stars),0) s FROM segments WHERE run_id=? AND child_id=?",
            (run.id, child),
        ).fetchone()["s"]
        assert seg_star_sum == 3


def test_two_tracks_independent(seeded, morning_routine_id, children):
    a, b = children[0]["id"], children[1]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        sa = run_service.check_in(conn, run.id, a)
        run_service.check_in(conn, run.id, b)
        # advance A once; B stays on segment 1
        run_service.complete_segment(conn, run.id, a, sa.id)
        cur_a = run_service.current_segment(conn, run.id, a)
        cur_b = run_service.current_segment(conn, run.id, b)
        assert cur_a.position == 1
        assert cur_b.position == 0
        # run still active (B not done)
        assert run_service.get_run(conn, run.id).state == "active"


def test_skip_excludes_and_advances(seeded, morning_routine_id, children):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        seg = run_service.check_in(conn, run.id, child)
        nxt = run_service.skip_segment(conn, seg.id)
        skipped = conn.execute("SELECT * FROM segments WHERE id=?", (seg.id,)).fetchone()
        assert skipped["state"] == "skipped"
        assert skipped["stars"] == 0
        assert nxt.position == 1


def test_close_run_marks_open_incomplete(seeded, morning_routine_id, children):
    child = children[0]["id"]
    with instance_conn(seeded) as conn:
        run = run_service.open_run(conn, morning_routine_id, "p")
        run_service.check_in(conn, run.id, child)
        run_service.close_run(conn, run.id)
        assert run_service.get_run(conn, run.id).state == "closed"
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM segments WHERE run_id=? AND state IN "
            "('pending','active','gate_open')",
            (run.id,),
        ).fetchone()
        assert remaining["c"] == 0


def test_only_one_run_at_a_time(seeded, morning_routine_id):
    with instance_conn(seeded) as conn:
        run_service.open_run(conn, morning_routine_id, "p")
        try:
            run_service.open_run(conn, morning_routine_id, "p")
            assert False, "should not open a second run"
        except run_service.RunError:
            pass
