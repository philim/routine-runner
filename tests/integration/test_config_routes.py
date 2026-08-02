"""Parent routine-configuration routes over HTTP (spec §12)."""

from __future__ import annotations

from app.db.instance_db import instance_conn
from app.services import routine_service, run_service


def test_create_routine_redirects_to_edit(parent_client, seeded):
    r = parent_client.post("/routines", data={"name": "weekend"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/edit" in r.headers["location"]

    edit = parent_client.get(r.headers["location"])
    assert edit.status_code == 200
    assert "weekend" in edit.text.lower()


def test_add_step_then_add_valid_gate(parent_client, seeded):
    parent_client.post("/routines", data={"name": "weekend"})
    with instance_conn(seeded) as conn:
        routine = [r for r in routine_service.list_all_routines(conn) if r.name == "weekend"][0]

    r1 = parent_client.post(
        f"/routines/{routine.id}/steps",
        data={"title": "Make bed", "icon": "🛏️", "kind": "task", "floor_seconds": "20"},
    )
    assert r1.status_code == 200
    assert "Make bed" in r1.text

    with instance_conn(seeded) as conn:
        task = routine_service.all_steps(conn, routine.id)[0]

    # a gate with no target is rejected (spec §4/§12)
    bad = parent_client.post(
        f"/routines/{routine.id}/steps",
        data={"title": "Check", "icon": "🔎", "kind": "gate", "on_reject_step_id": ""},
    )
    assert bad.status_code == 400

    good = parent_client.post(
        f"/routines/{routine.id}/steps",
        data={"title": "Check", "icon": "🔎", "kind": "gate",
              "on_reject_step_id": task.id},
    )
    assert good.status_code == 200
    assert "Check" in good.text


def test_move_floor_toggle_and_duplicate_over_http(parent_client, seeded):
    parent_client.post("/routines", data={"name": "weekend"})
    with instance_conn(seeded) as conn:
        routine = [r for r in routine_service.list_all_routines(conn) if r.name == "weekend"][0]
        s1 = routine_service.add_step(conn, routine.id, "A", None, "task", floor_seconds=30)
        s2 = routine_service.add_step(conn, routine.id, "B", None, "task")

    parent_client.post(f"/steps/{s2.id}/move", data={"direction": "up"})
    with instance_conn(seeded) as conn:
        steps = routine_service.all_steps(conn, routine.id)
    assert [s.title for s in steps] == ["B", "A"]

    parent_client.post(f"/steps/{s1.id}/floor", data={"delta": "15"})
    with instance_conn(seeded) as conn:
        assert routine_service.get_step(conn, s1.id).floor_seconds == 45

    parent_client.post(f"/steps/{s1.id}/toggle", data={"active": "false"})
    with instance_conn(seeded) as conn:
        assert routine_service.get_step(conn, s1.id).active == 0

    dup = parent_client.post(f"/steps/{s2.id}/duplicate")
    assert dup.status_code == 200
    assert "(copy)" in dup.text


def test_reorder_steps_over_http(parent_client, seeded):
    parent_client.post("/routines", data={"name": "weekend"})
    with instance_conn(seeded) as conn:
        routine = [r for r in routine_service.list_all_routines(conn) if r.name == "weekend"][0]
        a = routine_service.add_step(conn, routine.id, "A", None, "task")
        b = routine_service.add_step(conn, routine.id, "B", None, "task")
        c = routine_service.add_step(conn, routine.id, "C", None, "task")

    # drag C to the front: full ordering posted as a comma-separated id list
    r = parent_client.post(
        "/steps/reorder",
        data={"routine_id": routine.id, "ordered_ids": f"{c.id},{a.id},{b.id}"},
    )
    assert r.status_code == 200
    with instance_conn(seeded) as conn:
        steps = routine_service.all_steps(conn, routine.id)
    assert [s.title for s in steps] == ["C", "A", "B"]
    assert [s.position for s in steps] == [0, 1, 2]


def test_reorder_rejecting_a_gate_to_the_front_reverts(parent_client, seeded):
    """An ordering that would lead the routine with a gate is invalid — the
    server leaves positions untouched and re-renders, so the UI snaps back."""
    parent_client.post("/routines", data={"name": "weekend"})
    with instance_conn(seeded) as conn:
        routine = [r for r in routine_service.list_all_routines(conn) if r.name == "weekend"][0]
        task = routine_service.add_step(conn, routine.id, "Brush", None, "task")
        gate = routine_service.add_step(
            conn, routine.id, "Check", None, "gate", on_reject_step_id=task.id
        )

    r = parent_client.post(
        "/steps/reorder",
        data={"routine_id": routine.id, "ordered_ids": f"{gate.id},{task.id}"},
    )
    assert r.status_code == 200
    with instance_conn(seeded) as conn:
        steps = routine_service.all_steps(conn, routine.id)
    # order unchanged: task still leads, gate still follows
    assert [s.title for s in steps] == ["Brush", "Check"]


def test_editing_routine_does_not_affect_a_run_already_in_progress(
    parent_client, kiosk_client, seeded, bedtime_routine_id, children
):
    """Runs snapshot their step list at check-in (§5.1) — a later edit must
    never retroactively change a track that's already underway."""
    child = children[0]["id"]
    parent_client.post("/runs", data={"routine_id": bedtime_routine_id})
    kiosk_client.post("/kiosk/checkin", data={"child_id": child})
    with instance_conn(seeded) as conn:
        run = run_service.active_run(conn)
        before = [s.step_id for s in run_service.child_segments(conn, run.id, child)]

    parent_client.post(
        f"/routines/{bedtime_routine_id}/steps",
        data={"title": "New step", "icon": "✨", "kind": "task", "floor_seconds": "20"},
    )

    with instance_conn(seeded) as conn:
        after = [s.step_id for s in run_service.child_segments(conn, run.id, child)]
    assert before == after


def test_toggle_routine_active(parent_client, seeded):
    parent_client.post("/routines", data={"name": "weekend"})
    with instance_conn(seeded) as conn:
        routine = [r for r in routine_service.list_all_routines(conn) if r.name == "weekend"][0]
    r = parent_client.post(
        f"/routines/{routine.id}/toggle", data={"active": "false"}, follow_redirects=False
    )
    assert r.status_code == 303
    with instance_conn(seeded) as conn:
        assert routine_service.get_routine(conn, routine.id).active == 0
    # a disabled routine no longer appears on the kiosk-facing list
    with instance_conn(seeded) as conn:
        names = [r.name for r in routine_service.list_routines(conn)]
    assert "weekend" not in names


def test_config_routes_require_parent_auth(kiosk_client, parent_token, seeded):
    # parent_token must exist so this is scope enforcement (403), not the
    # unclaimed-household recovery redirect to /setup.
    assert kiosk_client.get("/routines").status_code == 403
    assert kiosk_client.post("/routines", data={"name": "x"}).status_code == 403
