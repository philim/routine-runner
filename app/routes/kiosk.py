"""Kiosk endpoints (spec §11). Renders HTML partials; the kiosk is a dumb renderer.

Phase 1: tile check-in, DONE advances, run-1 plain elapsed timer. NFC and
countdown rings arrive in later phases.
"""

from __future__ import annotations

from sqlite3 import Connection

from fastapi import APIRouter, Depends, Form, Request

from app.config import Config
from app.db.instance_db import instance_conn
from app.jobs import scheduler as scheduler_job
from app.models.device import Device
from app.routes.deps import get_config, require_kiosk, templates
from app.services import (
    clock,
    notify_service,
    routine_service,
    run_service,
    verification_service,
)
from app.services.event_bus import Event, bus

router = APIRouter()


def _on_gate_opened(request: Request, config: Config, segment) -> None:
    """Notify parents and start the escalation ladder when a gate opens (§6.4.1)."""
    notify_service.notify(config, "Check needed", "A verification is waiting.")
    bus.publish("parent", Event(name="state", data="review"))
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_job.schedule_gate_escalation(scheduler, config, segment.id)


def _kiosk_channel() -> str:
    return "kiosk"


def build_state(conn: Connection, config: Config) -> dict:
    """Assemble the full kiosk render context for the current run state."""
    run = run_service.active_run(conn)
    now = clock.now_ms()
    if run is None:
        return {"mode": "idle", "now_ms": now}

    routine = routine_service.get_routine(conn, run.routine_id)
    children_rows = conn.execute(
        "SELECT * FROM children WHERE active = 1 ORDER BY sort_order"
    ).fetchall()
    checked_in = {rc.child_id: rc for rc in run_service.run_children(conn, run.id)}

    if not checked_in:
        # ready mode: show check-in tiles for all active children
        tiles = [dict(r) for r in children_rows]
        return {"mode": "ready", "run": run, "routine": routine, "tiles": tiles, "now_ms": now}

    columns = []
    for r in children_rows:
        rc = checked_in.get(r["id"])
        if rc is None:
            continue
        segs = run_service.child_segments(conn, run.id, r["id"])
        cur = run_service.current_segment(conn, run.id, r["id"])
        step = None
        if cur is not None:
            srow = conn.execute("SELECT * FROM steps WHERE id = ?", (cur.step_id,)).fetchone()
            step = dict(srow) if srow else None
        done_count = sum(1 for s in segs if s.state in ("done", "skipped", "incomplete"))
        columns.append(
            {
                "child": dict(r),
                "run_child": rc,
                "segment": cur,
                "step": step,
                "total": len(segs),
                "done": done_count,
                "finished": rc.state == "completed",
            }
        )
    return {
        "mode": "active",
        "run": run,
        "routine": routine,
        "columns": columns,
        "now_ms": now,
    }


@router.get("/kiosk")
def kiosk(
    request: Request,
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        ctx = build_state(conn, config)
    ctx["request"] = request
    return templates.TemplateResponse(ctx["request"], "kiosk/shell.html", ctx)


@router.get("/kiosk/state")
def kiosk_state(
    request: Request,
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    """Full-state reconcile after reconnect (spec §8.3)."""
    with instance_conn(config) as conn:
        ctx = build_state(conn, config)
    ctx["request"] = request
    return templates.TemplateResponse(ctx["request"], "kiosk/board.html", ctx)


def _publish_kiosk_update(config: Config) -> None:
    bus.publish(_kiosk_channel(), Event(name="state", data="update"))


@router.post("/kiosk/checkin")
def checkin(
    request: Request,
    child_id: str = Form(default=""),
    nfc_token: str = Form(default=""),
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run = run_service.active_run(conn)
        if run is None:
            ctx = build_state(conn, config)
            ctx["request"] = request
            return templates.TemplateResponse(ctx["request"], "kiosk/board.html", ctx)
        resolved = child_id
        if not resolved and nfc_token:
            row = conn.execute(
                "SELECT id FROM children WHERE nfc_token = ? AND active = 1", (nfc_token,)
            ).fetchone()
            resolved = row["id"] if row else ""
        if resolved:
            run_service.check_in(conn, run.id, resolved)
        ctx = build_state(conn, config)
    ctx["request"] = request
    _publish_kiosk_update(config)
    return templates.TemplateResponse(ctx["request"], "kiosk/board.html", ctx)


@router.post("/kiosk/complete")
def complete(
    request: Request,
    child_id: str = Form(...),
    segment_id: str = Form(...),
    client_ts: int | None = Form(default=None),
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run = run_service.active_run(conn)
        if run is not None:
            seg = run_service._get_segment(conn, segment_id)
            # idempotent: ignore replays of an already-closed segment (§8.3)
            if seg is not None and seg.state == "active":
                at = _sanitise_client_ts(client_ts, seg.first_started_at)
                nxt = run_service.complete_segment(conn, run.id, child_id, segment_id, at)
                if nxt is not None and nxt.state == "gate_open":
                    _on_gate_opened(request, config, nxt)
        ctx = build_state(conn, config)
    ctx["request"] = request
    _publish_kiosk_update(config)
    return templates.TemplateResponse(ctx["request"], "kiosk/board.html", ctx)


@router.post("/kiosk/gate-nudge")
def gate_nudge(
    segment_id: str = Form(...),
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    """Child taps 'Ask again' — re-notify parents, rate-limited to once/30s (§6.4.1)."""
    with instance_conn(config) as conn:
        if verification_service.can_nudge(conn, segment_id):
            verification_service.record_nudge(conn, segment_id)
            allowed = True
        else:
            allowed = False
    if allowed:
        notify_service.notify(config, "Reminder", "A child is asking for a check.",
                              priority="high")
    return {"ok": True, "sent": allowed}


@router.post("/kiosk/heartbeat")
def heartbeat(device: Device = Depends(require_kiosk)):
    return {"ok": True}


def _sanitise_client_ts(client_ts: int | None, started_at: int | None) -> int | None:
    """Accept a client timestamp only within a sane window (spec §8.3)."""
    if client_ts is None:
        return None
    now = clock.now_ms()
    # reject future or pre-start timestamps; clamp to server now otherwise
    if started_at is not None and client_ts < started_at:
        return None
    if client_ts > now + 5000:
        return None
    return client_ts
