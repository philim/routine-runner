"""Background scheduling (technical plan §1, §5.4, §6.4.1).

Wraps APScheduler so the app can:
  * recompute pars weekly (Sunday 22:00),
  * nudge parents about stalled segments,
  * run the gate escalation ladder (re-notify + 5-minute auto-approve backstop).

Job callables open their own DB connections so they are thread-safe.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Config
from app.db.instance_db import instance_conn
from app.jobs import weekly_par
from app.services import clock, notify_service, verification_service
from app.services.event_bus import Event, bus

log = logging.getLogger("routine_runner.jobs.scheduler")

STALL_SCAN_SECONDS = 30


def start(config: Config) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        weekly_par.run, "cron", day_of_week="sun", hour=22, minute=0,
        args=[config], id="weekly_par", replace_existing=True,
    )
    scheduler.add_job(
        _scan_stalled, "interval", seconds=STALL_SCAN_SECONDS,
        args=[config], id="stall_monitor", replace_existing=True,
    )
    scheduler.start()
    return scheduler


def schedule_gate_escalation(scheduler, config: Config, segment_id: str) -> None:
    """On gate open: re-notify at 60s, auto-approve at 5min (spec §6.4.1)."""
    if scheduler is None:
        return
    from datetime import timedelta

    from app.services.clock import now_ms

    def _at(seconds: int):
        # APScheduler needs a wall-clock; derive from the authoritative clock.
        from datetime import UTC, datetime
        return datetime.fromtimestamp(now_ms() / 1000, UTC) + timedelta(seconds=seconds)

    scheduler.add_job(
        _renotify_gate, "date", run_date=_at(60), args=[config, segment_id],
        id=f"gate_renotify_{segment_id}", replace_existing=True,
    )
    scheduler.add_job(
        _auto_approve_gate, "date", run_date=_at(verification_service.AUTO_APPROVE_SECONDS),
        args=[config, segment_id], id=f"gate_auto_{segment_id}", replace_existing=True,
    )


# --- job bodies -------------------------------------------------------------

def _renotify_gate(config: Config, segment_id: str) -> None:
    with instance_conn(config) as conn:
        gate = conn.execute(
            "SELECT state FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()
        if gate is None or gate["state"] != "gate_open":
            return
    notify_service.notify(config, "Check still waiting", "A verification is still pending.",
                          priority="high")


def _auto_approve_gate(config: Config, segment_id: str) -> None:
    with instance_conn(config) as conn:
        result = verification_service.auto_approve(conn, segment_id)
    if result is not None:
        notify_service.notify(config, "Gate auto-approved",
                              "A check timed out and was auto-approved.")
        bus.publish("kiosk", Event(name="state", data="update"))


def _scan_stalled(config: Config) -> None:
    """Notify once per segment that crosses 1.5×par (stalled, spec §5.5)."""
    now = clock.now_ms()
    with instance_conn(config) as conn:
        rows = conn.execute(
            "SELECT s.*, c.name AS child_name FROM segments s "
            "JOIN children c ON c.id = s.child_id "
            "WHERE s.state = 'active' AND s.par_seconds IS NOT NULL",
        ).fetchall()
        for s in rows:
            if not s["first_started_at"]:
                continue
            elapsed = (now - s["first_started_at"]) / 1000
            if elapsed <= 1.5 * s["par_seconds"]:
                continue
            already = conn.execute(
                "SELECT 1 FROM events WHERE type = 'segment_stalled' "
                "AND payload_json LIKE ?",
                (f'%{s["id"]}%',),
            ).fetchone()
            if already:
                continue
            conn.execute(
                "INSERT INTO events "
                "(id, run_id, child_id, type, payload_json, occurred_at, source) "
                "VALUES (lower(hex(randomblob(16))), ?, ?, 'segment_stalled', ?, ?, 'system')",
                (s["run_id"], s["child_id"], f'{{"segment_id": "{s["id"]}"}}', now),
            )
            notify_service.notify(
                config, "Stalled", f'{s["child_name"]} is stuck on a step.', priority="high"
            )
