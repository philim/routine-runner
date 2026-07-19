"""Weekly steady-state par recompute (spec §5.4, technical plan §5.2).

Runs Sunday 22:00 — never mid-week, never mid-run — so par is stable and legible
for seven days.
"""

from __future__ import annotations

import logging

from app.config import Config
from app.db.instance_db import instance_conn
from app.services import par_service

log = logging.getLogger("routine_runner.jobs.weekly_par")


def run(config: Config) -> int:
    with instance_conn(config) as conn:
        updated = par_service.recompute_weekly(conn)
    log.info("weekly par recompute updated %d pars", updated)
    return updated
