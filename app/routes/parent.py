"""Parent dashboard and live-run controls (spec §11, Phase 1 subset)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.config import Config
from app.db import main_db
from app.db.instance_db import instance_conn
from app.models.device import Device
from app.routes.deps import (
    current_device,
    get_config,
    require_parent,
    scrub_stale_role_cookies,
    templates,
)
from app.routes.kiosk import build_state
from app.services import par_service, routine_service, run_service, verification_service
from app.services.event_bus import Event, bus

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    response: Response,
    device: Device | None = Depends(current_device),
    config: Config = Depends(get_config),
):
    """Role-aware entry: parent dashboard, kiosk redirect, or bootstrap."""
    scrub_stale_role_cookies(request, response, config)
    if device is None:
        if not main_db.has_active_parents(config.main_db_path):
            return RedirectResponse("/setup", status_code=303)
        raise HTTPException(status_code=403, detail="parent device required")
    if device.role == "kiosk":
        return RedirectResponse("/kiosk", status_code=303)
    if device.role != "parent":
        raise HTTPException(status_code=403, detail="parent device required")

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


# --- gates (spec §6.4, §11) -------------------------------------------------

@router.get("/gates")
def gates(
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        open_gates = verification_service.open_gates(conn)
    return templates.TemplateResponse(
        request, "parent/review.html", {"gates": open_gates}
    )


@router.post("/gates/{segment_id}/approve")
def approve_gate(
    segment_id: str,
    quality_stars: int = Form(default=0),
    note: str | None = Form(default=None),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        verification_service.approve(conn, segment_id, quality_stars, note, parent.id)
    bus.publish("kiosk", Event(name="state", data="update"))
    return {"ok": True}


@router.post("/gates/{segment_id}/reject")
def reject_gate(
    segment_id: str,
    note: str | None = Form(default=None),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        verification_service.reject(conn, segment_id, note, parent.id)
    bus.publish("kiosk", Event(name="state", data="update"))
    return {"ok": True}


# --- pars (spec §5.4, §11, §12) ---------------------------------------------

@router.get("/pars")
def pars(
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        rows = conn.execute(
            "SELECT p.*, c.name AS child_name, s.title AS step_title "
            "FROM pars p JOIN children c ON c.id = p.child_id "
            "JOIN steps s ON s.id = p.step_id "
            "WHERE p.superseded_at IS NULL ORDER BY c.name, s.position"
        ).fetchall()
        current = [dict(r) for r in rows]
    return templates.TemplateResponse(request, "parent/pars.html", {"pars": current})


@router.post("/pars/{par_id}/freeze")
def freeze_par(
    par_id: str,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        par_service.freeze(conn, par_id)
    return {"ok": True}


@router.post("/pars/reset")
def reset_par(
    child_id: str = Form(...),
    step_id: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        par_service.reset(conn, child_id, step_id)
    return {"ok": True}
