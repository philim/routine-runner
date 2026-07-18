"""Parent dashboard and live-run controls (spec §11, Phase 1 subset)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from app.config import Config
from app.db.instance_db import instance_conn
from app.models.device import Device
from app.routes.deps import get_config, require_parent, templates
from app.routes.kiosk import build_state
from app.services import routine_service, run_service
from app.services.event_bus import Event, bus

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        routines = routine_service.list_routines(conn)
        run = run_service.active_run(conn)
    return templates.TemplateResponse(
        request,
        "parent/dashboard.html",
        {"routines": routines, "active_run": run},
    )


@router.post("/runs")
def start_run(
    request: Request,
    routine_id: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run = run_service.open_run(conn, routine_id, started_by=parent.id)
    bus.publish("kiosk", Event(name="state", data="update"))
    return templates.TemplateResponse(
        request, "partials/run_started.html", {"run": run}
    )


@router.get("/runs/{run_id}/live")
def live(
    run_id: str,
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run = run_service.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404)
        ctx = build_state(conn, config)
    ctx["request"] = request
    return templates.TemplateResponse(ctx["request"], "parent/run_live.html", ctx)


@router.post("/runs/{run_id}/pause")
def pause(
    run_id: str,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run = run_service.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404)
        if run.state == "paused":
            run_service.resume_run(conn, run_id)
        else:
            run_service.pause_run(conn, run_id)
    bus.publish("kiosk", Event(name="state", data="update"))
    return {"ok": True}


@router.post("/runs/{run_id}/close")
def close(
    run_id: str,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run_service.close_run(conn, run_id)
    bus.publish("kiosk", Event(name="state", data="update"))
    return {"ok": True}


@router.post("/segments/{segment_id}/skip")
def skip(
    segment_id: str,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        run_service.skip_segment(conn, segment_id)
    bus.publish("kiosk", Event(name="state", data="update"))
    return {"ok": True}
