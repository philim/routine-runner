"""End-to-end Phase 1 walking skeleton via the ASGI client."""

from __future__ import annotations

import pytest

from app.db.instance_db import instance_conn
from app.services import clock, run_service


class FakeClock:
    def __init__(self):
        self.t = clock.now_ms()  # anchor to a realistic starting point

    def advance(self, seconds):
        self.t += seconds * 1000

    def now_ms(self):
        return self.t


@pytest.fixture
def fc(monkeypatch):
    """Deterministic clock so completions can clear the 30s debounce without sleeping."""
    c = FakeClock()
    monkeypatch.setattr(clock, "now_ms", c.now_ms)
    return c


def test_full_morning_run_over_http(parent_client, kiosk_client, seeded,
                                    morning_routine_id, children, fc):
    # parent starts the run
    r = parent_client.post("/runs", data={"routine_id": morning_routine_id})
    assert r.status_code == 200
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
    assert run is not None

    # kiosk shows an independent column per child, each offering a check-in tile
    board = kiosk_client.get("/kiosk/state")
    assert board.status_code == 200
    assert board.text.count("Tap to start") == len(children)
    # each column is a preserved wrapper with its own self-refreshing body, so a
    # board re-render never tears down a sibling's live timer
    for c in children:
        assert f'id="kiosk-col-{c["id"]}"' in board.text
        assert f'id="kiosk-colc-{c["id"]}"' in board.text
    assert board.text.count("hx-preserve") == len(children)
    assert "/kiosk/column/" in board.text

    # child A checks in
    a = children[0]["id"]
    r = kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    assert r.status_code == 200
    assert "DONE" in r.text

    # walk the whole track: DONE tasks, approve the gate when it blocks
    for _ in range(20):
        with instance_conn(seeded) as conn:
            cur = run_service.current_segment(conn, run.id, a)
        if cur is None:
            break
        if cur.state == "gate_open":
            r = parent_client.post(f"/gates/{cur.id}/approve", data={"quality_stars": 2})
            assert r.status_code == 200
        else:
            fc.advance(35)  # clear the completion debounce
            r = kiosk_client.post(
                "/kiosk/complete", data={"child_id": a, "segment_id": cur.id}
            )
            assert r.status_code == 200

    with instance_conn(seeded) as conn:
        rc = [x for x in run_service.run_children(conn, run.id) if x.child_id == a][0]
    assert rc.state == "completed"
    assert rc.stars > 0


def test_nfc_checkin_resolves_child(kiosk_client, parent_client, seeded,
                                    morning_routine_id, children):
    # give child A an nfc token
    a = children[0]["id"]
    with instance_conn(seeded) as conn:
        conn.execute("UPDATE children SET nfc_token=? WHERE id=?", ("tok-a", a))
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    r = kiosk_client.post("/kiosk/checkin", data={"nfc_token": "tok-a"})
    assert r.status_code == 200
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        rcs = run_service.run_children(conn, run.id)
    assert any(rc.child_id == a for rc in rcs)


def test_children_check_in_and_run_independently(parent_client, kiosk_client, seeded,
                                                 morning_routine_id, children):
    a, b = children[0]["id"], children[1]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})

    # child A checks in first; the run becomes active
    ra = kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    assert ra.status_code == 200
    assert "DONE" in ra.text  # A's own column shows their routine

    # child B is NOT locked out: their column still offers a check-in tile
    colb = kiosk_client.get(f"/kiosk/column/{b}")
    assert colb.status_code == 200
    assert "Tap to start" in colb.text

    # child B checks in while A is mid-routine — both now run concurrently
    rb = kiosk_client.post("/kiosk/checkin", data={"child_id": b})
    assert rb.status_code == 200
    assert "DONE" in rb.text

    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        assert run is not None
        rcs = {rc.child_id: rc for rc in run_service.run_children(conn, run.id)}
        assert set(rcs) == {a, b}
        assert all(rc.state == "active" for rc in rcs.values())
        # each child has their own independent current segment
        assert run_service.current_segment(conn, run.id, a) is not None
        assert run_service.current_segment(conn, run.id, b) is not None


def test_one_childs_action_does_not_touch_the_other_column(parent_client, kiosk_client,
                                                           seeded, morning_routine_id,
                                                           children, fc):
    a, b = children[0]["id"], children[1]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    kiosk_client.post("/kiosk/checkin", data={"child_id": b})

    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        seg_a = run_service.current_segment(conn, run.id, a)

    # completing A's step returns ONLY A's column fragment, not B's
    fc.advance(35)  # clear the completion debounce
    r = kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": seg_a.id})
    assert r.status_code == 200
    assert f'id="kiosk-colc-{a}"' in r.text
    assert f'id="kiosk-colc-{b}"' not in r.text


def test_complete_is_idempotent(parent_client, kiosk_client, seeded,
                                morning_routine_id, children, fc):
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        cur = run_service.current_segment(conn, run.id, a)
    fc.advance(35)  # clear the completion debounce
    # send the same completion twice; the second is ignored
    kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": cur.id})
    kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": cur.id})
    with instance_conn(seeded) as conn:
        after = run_service.current_segment(conn, run.id, a)
    # advanced exactly one step, not two
    assert after.position == 1


def test_completion_within_30s_is_silently_ignored(
    parent_client, kiosk_client, seeded, morning_routine_id, children
):
    """A too-fast tap doesn't error the kiosk — it just doesn't advance (debounce)."""
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        cur = run_service.current_segment(conn, run.id, a)
    r = kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": cur.id})
    assert r.status_code == 200  # no error surfaced to the kiosk
    with instance_conn(seeded) as conn:
        still = run_service.current_segment(conn, run.id, a)
    assert still.id == cur.id
    assert still.state == "active"


def test_live_view_survives_run_closing(parent_client, kiosk_client, seeded,
                                        morning_routine_id, children):
    """Regression: /runs/{id}/live used to crash with a Jinja UndefinedError
    once the run it names was no longer *the* active run (build_state()
    returned mode="idle" with no `run` key at all)."""
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
    run_id = run.id

    live = parent_client.get(f"/runs/{run_id}/live")
    assert live.status_code == 200
    assert "Not checked in yet" in live.text  # neither child has checked in

    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    with instance_conn(seeded) as conn:
        run_service.close_run(conn, run_id)

    # the run is closed and no longer "active" — the live view must still render
    live_after_close = parent_client.get(f"/runs/{run_id}/live")
    assert live_after_close.status_code == 200
    assert "closed" in live_after_close.text


def test_double_close_is_a_no_op_not_a_500(parent_client, seeded, morning_routine_id):
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
    first = parent_client.post(f"/runs/{run.id}/close")
    assert first.status_code == 200
    second = parent_client.post(f"/runs/{run.id}/close")
    assert second.status_code == 200
    assert second.json().get("already_closed") is True


def test_skip_on_already_resolved_segment_is_a_no_op_not_a_500(
    parent_client, kiosk_client, seeded, morning_routine_id, children, fc
):
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        cur = run_service.current_segment(conn, run.id, a)
    # kiosk completes the step itself (simulating a race with a parent's stale
    # "Skip" button already rendered for that same segment)
    fc.advance(35)  # clear the completion debounce
    kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": cur.id})

    r = parent_client.post(f"/segments/{cur.id}/skip")
    assert r.status_code == 200
    assert r.json().get("already_resolved") is True


def test_pause_on_closed_run_returns_409_not_500(parent_client, seeded, morning_routine_id):
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        run_service.close_run(conn, run.id)
    r = parent_client.post(f"/runs/{run.id}/pause")
    assert r.status_code == 409


def test_starting_a_second_run_is_a_friendly_conflict_not_a_500(
    parent_client, seeded, morning_routine_id
):
    """Regression: a second tab/device racing to start a run used to 500 with
    an uncaught RunError("a run is already open")."""
    first = parent_client.post("/runs", data={"routine_id": morning_routine_id})
    assert first.status_code == 200

    second = parent_client.post("/runs", data={"routine_id": morning_routine_id})
    assert second.status_code == 409
    assert "already in progress" in second.text
    assert "/live" in second.text  # points at the run that IS running

    # exactly one run was created
    with instance_conn(seeded) as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    assert n == 1


def _complete_full_track(client, seeded, run_id, child_id, approve_client, fc=None):
    """Drive one child's entire track to completion over HTTP (helper for the
    summary tests below)."""
    for _ in range(20):
        with instance_conn(seeded) as conn:
            cur = run_service.current_segment(conn, run_id, child_id)
        if cur is None:
            break
        if cur.state == "gate_open":
            approve_client.post(f"/gates/{cur.id}/approve", data={"quality_stars": 2})
        else:
            if fc is not None:
                fc.advance(35)  # clear the completion debounce
            client.post("/kiosk/complete", data={"child_id": child_id, "segment_id": cur.id})


def test_kiosk_shows_run_summary_with_stars_and_step_times_when_finished(
    parent_client, kiosk_client, seeded, morning_routine_id, children, fc
):
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    _complete_full_track(kiosk_client, seeded, run.id, a, parent_client, fc)

    col = kiosk_client.get(f"/kiosk/column/{a}")
    assert col.status_code == 200
    assert "⭐" in col.text
    # each completed task step is listed with its title and elapsed/par time
    for title in ("Get dressed", "Brush teeth", "Hair", "Shoes on"):
        assert title in col.text
    assert "Not reached" not in col.text


def test_kiosk_summary_survives_run_closing_then_expires_after_grace_window(
    parent_client, kiosk_client, seeded, morning_routine_id, children, fc
):
    """The kiosk keeps showing a finished run's stars/par summary for a grace
    period after it closes (spec §7), instead of snapping straight to idle."""
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    _complete_full_track(kiosk_client, seeded, run.id, a, parent_client, fc)

    # parent ends the run early; child B never checked in
    parent_client.post(f"/runs/{run.id}/close")

    board = kiosk_client.get("/kiosk/state")
    assert board.status_code == 200
    assert "⭐" in board.text  # A's summary is still showing
    assert "Didn't check in" in board.text  # B is shown as not having joined

    # once the grace window has elapsed, the board goes truly idle again
    with instance_conn(seeded) as conn:
        closed_run = run_service.get_run(conn, run.id)
        conn.execute(
            "UPDATE runs SET closed_at = ? WHERE id = ?",
            (closed_run.closed_at - 11 * 60 * 1000, run.id),
        )
    stale_board = kiosk_client.get("/kiosk/state")
    assert stale_board.status_code == 200
    assert "Waiting for a routine to start" in stale_board.text
