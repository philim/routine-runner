"""Kiosk endpoints (spec §11). Renders HTML partials; the kiosk is a dumb renderer.

Phase 1: tile check-in, DONE advances, run-1 plain elapsed timer. NFC and
countdown rings arrive in later phases.
"""

from __future__ import annotations

import math
from sqlite3 import Connection

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

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


# How long a finished run's summary (stars, par times) stays on the kiosk
# before a truly idle screen takes over — long enough for kids to see the
# results, short enough not to confuse the next day (spec §7).
_SUMMARY_GRACE_MS = 10 * 60 * 1000


# Countdown-ring geometry (spec §5.5, §5.6) — mirrors static/js/kiosk.js so the
# server can render the *correct* initial ring state. A freshly-swapped SVG
# node (every checkin/DONE/poll replaces the column via hx-swap="outerHTML")
# has no prior inline stroke-dashoffset; if we left it at the browser default
# (0, i.e. a full/"just started" ring) the CSS transition would visibly
# animate from that wrong starting point to the real one on every swap —
# looking exactly like the clock had reset. Rendering the true value up front
# means there is nothing incorrect to animate away from.
_RING_R = 52
_RING_CIRC = 2 * math.pi * _RING_R


def _ring_state_for(ratio: float) -> str:
    if ratio < 0.75:
        return "on_pace"
    if ratio <= 1.0:
        return "closing"
    if ratio <= 1.5:
        return "over"
    return "stalled"


def _mmss(total_seconds: int) -> str:
    m, s = divmod(max(0, int(total_seconds)), 60)
    return f"{m}:{s:02d}"


def _ring_context(segment, display_mode: str, now_ms: int) -> dict:
    """Countdown-ring render values for an active task segment's current instant."""
    started = segment.first_started_at or now_ms
    par = segment.par_seconds
    elapsed = max(0, (now_ms - started) // 1000)
    numeric = display_mode == "ring_numeric"

    if par:
        ratio = elapsed / par
        remaining = max(0, par - elapsed)
        frac = max(0.0, min(1.0, remaining / par))
        dashoffset = _RING_CIRC * (1 - frac)
        state = _ring_state_for(ratio)
        text = _mmss(remaining) if ratio <= 1.0 else f"+{_mmss(elapsed - par)}"
    else:
        # run 1: no par yet — plain elapsed timer, neutral (spec §5.2)
        dashoffset = 0
        state = "none"
        text = _mmss(elapsed)

    return {
        "circumference": round(_RING_CIRC, 3),
        "dashoffset": round(dashoffset, 3),
        "state": state,
        "text": text if numeric else "",
        "text_visible": numeric,
    }


def _step_breakdown(conn: Connection, run_id: str, child_id: str) -> list[dict]:
    """Per-task-step results for a finished track's summary (title, times, stars)."""
    rows = conn.execute(
        "SELECT seg.state, seg.elapsed_seconds, seg.par_seconds, seg.stars, "
        "st.title, st.icon FROM segments seg "
        "JOIN steps st ON st.id = seg.step_id "
        "WHERE seg.run_id = ? AND seg.child_id = ? AND seg.kind = 'task' "
        "ORDER BY seg.position",
        (run_id, child_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _column_context(conn: Connection, run, child_row, checked_in: dict) -> dict:
    """Build the render context for a single child's column.

    Each child track is independent: a child who has not checked in yet gets a
    ``checked_in = False`` column (rendered as a tap-in tile) even while their
    sibling is mid-routine, so both sides can run at the same time (spec §5.1).
    """
    rc = checked_in.get(child_row["id"])
    col = {
        "child": dict(child_row),
        "checked_in": rc is not None,
        "run_child": rc,
        "segment": None,
        "step": None,
        "ring": None,
        "total": 0,
        "done": 0,
        "finished": False,
        "steps": None,
        "run_closed": run.state in ("closed", "abandoned"),
    }
    if rc is None:
        return col

    segs = run_service.child_segments(conn, run.id, child_row["id"])
    cur = run_service.current_segment(conn, run.id, child_row["id"])
    step = None
    if cur is not None:
        srow = conn.execute("SELECT * FROM steps WHERE id = ?", (cur.step_id,)).fetchone()
        step = dict(srow) if srow else None
    col["segment"] = cur
    col["step"] = step
    col["total"] = len(segs)
    col["done"] = sum(1 for s in segs if s.state in ("done", "skipped", "incomplete"))
    col["finished"] = rc.state == "completed"
    if col["finished"]:
        # run summary (spec §7): each step's time vs par and stars earned
        col["steps"] = _step_breakdown(conn, run.id, child_row["id"])
    elif cur is not None and cur.state == "active":
        col["ring"] = _ring_context(cur, child_row["display_mode"], clock.now_ms())
    return col


def _columns_for_run(conn: Connection, run) -> list[dict]:
    """Build one column per active child for a specific run.

    Independent of whether ``run`` is *the* currently-active run — the parent
    live view keeps viewing a run's final state after it closes (spec §11).
    """
    children_rows = conn.execute(
        "SELECT * FROM children WHERE active = 1 ORDER BY sort_order"
    ).fetchall()
    checked_in = {rc.child_id: rc for rc in run_service.run_children(conn, run.id)}
    return [_column_context(conn, run, r, checked_in) for r in children_rows]


def _current_or_summary_run(conn: Connection):
    """The active run, or a just-closed one still within its summary grace window.

    Keeps the kiosk showing the last run's stars/par-time summary for a while
    after it closes instead of snapping straight to idle (§7).
    """
    run = run_service.active_run(conn)
    if run is not None:
        return run
    closed = run_service.last_closed_run(conn)
    if closed is not None and closed.closed_at is not None:
        if clock.now_ms() - closed.closed_at < _SUMMARY_GRACE_MS:
            return closed
    return None


def build_state(conn: Connection, config: Config) -> dict:
    """Assemble the full kiosk render context for the current run state.

    While a run is active (or a just-finished one is still in its summary
    grace window) every active child gets its own column, so the two sides
    stay independent; the board only distinguishes ``idle`` from ``active``
    at the top level.
    """
    run = _current_or_summary_run(conn)
    now = clock.now_ms()
    if run is None:
        return {"mode": "idle", "now_ms": now}

    routine = routine_service.get_routine(conn, run.routine_id)
    columns = _columns_for_run(conn, run)
    return {
        "mode": "active",
        "run": run,
        "routine": routine,
        "columns": columns,
        "now_ms": now,
    }


def build_column(conn: Connection, child_id: str) -> dict | None:
    """Render context for one child's column, or ``None`` when unavailable.

    Returns ``None`` if there is no active/recently-closed run or the child is
    not an active child — callers use that to fall back to a board-level
    refresh.
    """
    run = _current_or_summary_run(conn)
    if run is None:
        return None
    child_row = conn.execute(
        "SELECT * FROM children WHERE id = ? AND active = 1", (child_id,)
    ).fetchone()
    if child_row is None:
        return None
    checked_in = {rc.child_id: rc for rc in run_service.run_children(conn, run.id)}
    return _column_context(conn, run, child_row, checked_in)


def build_run_view(conn: Connection, run_id: str) -> dict | None:
    """Render context for the parent's live view of a *specific* run.

    Unlike :func:`build_state`, this looks the run up directly by id rather
    than via :func:`run_service.active_run`, so the view keeps rendering that
    run's final column states (stars, "finished") after it closes instead of
    crashing once it's no longer the single active run (§11).
    """
    run = run_service.get_run(conn, run_id)
    if run is None:
        return None
    routine = routine_service.get_routine(conn, run.routine_id)
    columns = _columns_for_run(conn, run)
    return {
        "run": run,
        "routine": routine,
        "columns": columns,
        "finished": run.state in ("closed", "abandoned"),
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


@router.get("/kiosk/column/{child_id}")
def kiosk_column(
    child_id: str,
    request: Request,
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    """Render a single child's column so each side refreshes independently."""
    with instance_conn(config) as conn:
        col = build_column(conn, child_id)
    return _render_column(request, col)


def _render_column(request: Request, col: dict | None) -> HTMLResponse:
    """Return one child's column fragment, or empty when the run is gone.

    An empty body lets the still-polling board wrapper reconcile the top-level
    idle/active transition on its next tick.
    """
    if col is None:
        return HTMLResponse("")
    # Embedded on every response so the kiosk can measure clock skew (spec
    # §5.7) and render countdowns from server time, not the device's own
    # clock — a drifting kiosk clock (plausible on the "retired Android
    # phone" hardware in spec §3) would otherwise make kiosk.js's live tick
    # visibly disagree with the value this same response just rendered.
    ctx = {"request": request, "col": col, "now_ms": clock.now_ms()}
    return templates.TemplateResponse(request, "kiosk/column.html", ctx)


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
        resolved = child_id
        if not resolved and nfc_token:
            row = conn.execute(
                "SELECT id FROM children WHERE nfc_token = ? AND active = 1", (nfc_token,)
            ).fetchone()
            resolved = row["id"] if row else ""
        if run is not None and resolved:
            # idempotent: a duplicate tap (or a closed run) leaves state as-is (§8.3)
            try:
                run_service.check_in(conn, run.id, resolved)
            except run_service.RunError:
                pass
        col = build_column(conn, resolved) if resolved else None
    _publish_kiosk_update(config)
    return _render_column(request, col)


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
                try:
                    nxt = run_service.complete_segment(conn, run.id, child_id, segment_id, at)
                except run_service.DebounceError:
                    # a task can't be completed within 30s of starting; silently
                    # ignore the touch rather than surface an error to the child
                    pass
                else:
                    if nxt is not None and nxt.state == "gate_open":
                        _on_gate_opened(request, config, nxt)
        col = build_column(conn, child_id)
    _publish_kiosk_update(config)
    return _render_column(request, col)


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
