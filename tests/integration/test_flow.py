"""End-to-end Phase 1 walking skeleton via the ASGI client."""

from __future__ import annotations

from app.db.instance_db import instance_conn
from app.services import run_service


def test_full_morning_run_over_http(parent_client, kiosk_client, seeded,
                                    morning_routine_id, children):
    # parent starts the run
    r = parent_client.post("/runs", data={"routine_id": morning_routine_id})
    assert r.status_code == 200
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
    assert run is not None

    # kiosk shows ready mode with both tiles
    board = kiosk_client.get("/kiosk/state")
    assert board.status_code == 200
    assert "tap in" in board.text

    # child A checks in
    a = children[0]["id"]
    r = kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    assert r.status_code == 200
    assert "DONE" in r.text

    # complete every segment for A via the kiosk
    for _ in range(10):
        with instance_conn(seeded) as conn:
            cur = run_service.current_segment(conn, run.id, a)
        if cur is None:
            break
        r = kiosk_client.post(
            "/kiosk/complete", data={"child_id": a, "segment_id": cur.id}
        )
        assert r.status_code == 200

    with instance_conn(seeded) as conn:
        rc = [x for x in run_service.run_children(conn, run.id) if x.child_id == a][0]
    assert rc.state == "completed"
    assert rc.stars == 4


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


def test_complete_is_idempotent(parent_client, kiosk_client, seeded,
                                morning_routine_id, children):
    a = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": morning_routine_id})
    kiosk_client.post("/kiosk/checkin", data={"child_id": a})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        cur = run_service.current_segment(conn, run.id, a)
    # send the same completion twice; the second is ignored
    kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": cur.id})
    kiosk_client.post("/kiosk/complete", data={"child_id": a, "segment_id": cur.id})
    with instance_conn(seeded) as conn:
        after = run_service.current_segment(conn, run.id, a)
    # advanced exactly one step, not two
    assert after.position == 1
