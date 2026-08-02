"""Parent routine/step configuration (spec §12, technical plan Phase 4).

Fast to use one-handed at 22:40: drag-free (up/down buttons instead of a drag
library), floor time via +/- steppers, disable rather than delete so history
is preserved. Editing a routine never touches an active run — runs snapshot
their step list at check-in (``run_service.check_in``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import Config
from app.db.instance_db import instance_conn
from app.models.device import Device
from app.routes.deps import get_config, require_parent, templates
from app.services import routine_service
from app.services.event_bus import Event, bus

router = APIRouter()


def _publish_kiosk_update(config: Config) -> None:
    # a routine/step edit can't affect an in-progress run (snapshotted at
    # check-in), but the kiosk's "ready" tile list reflects active routines.
    bus.publish("kiosk", Event(name="state", data="update"))


@router.get("/routines")
def routines_index(
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        rs = routine_service.list_all_routines(conn)
    return templates.TemplateResponse(request, "parent/routines.html", {"routines": rs})


@router.post("/routines")
def create_routine(
    name: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        try:
            routine = routine_service.create_routine(conn, name)
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    _publish_kiosk_update(config)
    # straight into editing — the natural next step after naming a routine
    return RedirectResponse(f"/routines/{routine.id}/edit", status_code=303)


@router.post("/routines/{routine_id}/toggle")
def toggle_routine(
    routine_id: str,
    active: bool = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        routine_service.set_routine_active(conn, routine_id, active)
    _publish_kiosk_update(config)
    return RedirectResponse("/routines", status_code=303)


def _edit_context(conn, routine_id: str) -> dict:
    routine = routine_service.get_routine(conn, routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="routine not found")
    steps = routine_service.all_steps(conn, routine_id)
    # task steps that could serve as a *later* gate's on_reject target, per step
    task_steps = [s for s in steps if s.kind == "task"]
    return {"routine": routine, "steps": steps, "task_steps": task_steps}


@router.get("/routines/{routine_id}/edit")
def edit_routine(
    routine_id: str,
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        ctx = _edit_context(conn, routine_id)
    ctx["request"] = request
    return templates.TemplateResponse(request, "parent/routine_edit.html", ctx)


def _render_steps_partial(request: Request, config: Config, routine_id: str):
    with instance_conn(config) as conn:
        ctx = _edit_context(conn, routine_id)
    ctx["request"] = request
    return templates.TemplateResponse(request, "partials/steps_table.html", ctx)


@router.post("/routines/{routine_id}/steps")
def add_step(
    routine_id: str,
    request: Request,
    title: str = Form(...),
    icon: str = Form(default=""),
    kind: str = Form(...),
    floor_seconds: int = Form(default=routine_service.DEFAULT_FLOOR_SECONDS),
    on_reject_step_id: str = Form(default=""),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        try:
            routine_service.add_step(
                conn, routine_id, title, icon or None, kind,
                floor_seconds=floor_seconds if kind == "task" else None,
                on_reject_step_id=on_reject_step_id or None,
            )
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    _publish_kiosk_update(config)
    return _render_steps_partial(request, config, routine_id)


@router.post("/steps/reorder")
def reorder_steps(
    request: Request,
    routine_id: str = Form(...),
    ordered_ids: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    """Persist a drag-to-reorder drop (spec §11).

    Registered before ``/steps/{step_id}`` so the literal path wins over the
    parameterised one. ``ordered_ids`` is the comma-separated step id list in
    its new order; an ordering that breaks the gate rules is rejected and we
    re-render the unchanged table so the dragged row snaps back.
    """
    ids = [i for i in ordered_ids.split(",") if i]
    with instance_conn(config) as conn:
        try:
            routine_service.reorder_steps(conn, routine_id, ids)
        except routine_service.ConfigError:
            return _render_steps_partial(request, config, routine_id)
    _publish_kiosk_update(config)
    return _render_steps_partial(request, config, routine_id)


@router.post("/steps/{step_id}")
def update_step(
    step_id: str,
    request: Request,
    title: str = Form(...),
    icon: str = Form(default=""),
    on_reject_step_id: str = Form(default=""),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        step = routine_service.get_step(conn, step_id)
        if step is None:
            raise HTTPException(status_code=404, detail="step not found")
        try:
            routine_service.update_step(
                conn, step_id, title=title, icon=icon or None,
                on_reject_step_id=on_reject_step_id or None,
            )
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        routine_id = step.routine_id
    _publish_kiosk_update(config)
    return _render_steps_partial(request, config, routine_id)


@router.post("/steps/{step_id}/floor")
def adjust_floor(
    step_id: str,
    request: Request,
    delta: int = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        step = routine_service.get_step(conn, step_id)
        if step is None:
            raise HTTPException(status_code=404, detail="step not found")
        try:
            routine_service.adjust_floor(conn, step_id, delta)
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        routine_id = step.routine_id
    return _render_steps_partial(request, config, routine_id)


@router.post("/steps/{step_id}/toggle")
def toggle_step(
    step_id: str,
    request: Request,
    active: bool = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        step = routine_service.get_step(conn, step_id)
        if step is None:
            raise HTTPException(status_code=404, detail="step not found")
        try:
            routine_service.set_step_active(conn, step_id, active)
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        routine_id = step.routine_id
    _publish_kiosk_update(config)
    return _render_steps_partial(request, config, routine_id)


@router.post("/steps/{step_id}/duplicate")
def duplicate_step(
    step_id: str,
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        step = routine_service.get_step(conn, step_id)
        if step is None:
            raise HTTPException(status_code=404, detail="step not found")
        try:
            routine_service.duplicate_step(conn, step_id)
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        routine_id = step.routine_id
    _publish_kiosk_update(config)
    return _render_steps_partial(request, config, routine_id)


@router.post("/steps/{step_id}/move")
def move_step(
    step_id: str,
    request: Request,
    direction: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    with instance_conn(config) as conn:
        step = routine_service.get_step(conn, step_id)
        if step is None:
            raise HTTPException(status_code=404, detail="step not found")
        try:
            routine_service.move_step(conn, step_id, direction)
        except routine_service.ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        routine_id = step.routine_id
    _publish_kiosk_update(config)
    return _render_steps_partial(request, config, routine_id)
