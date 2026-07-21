"""FastAPI application entry point (spec §8)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import INSECURE_JWT_SECRET, load_config
from app.db import main_db
from app.jobs import scheduler as scheduler_job
from app.routes import enrol, events, kiosk, parent, parent_auth

STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("routine_runner.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    main_db.bootstrap(config)
    app.state.config = config
    if config.jwt_secret == INSECURE_JWT_SECRET:
        log.warning(
            "RR_JWT_SECRET is unset — using the insecure development default. "
            "Set a strong secret before deploying."
        )
    if config.dev_mode:
        log.warning(
            "RR_DEV_MODE is ON: /parent/dev-login grants a parent session with "
            "NO authentication. Never enable this in production."
        )
    app.state.scheduler = scheduler_job.start(config)
    try:
        yield
    finally:
        app.state.scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Routine Runner", lifespan=lifespan)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(enrol.router)
    app.include_router(parent_auth.router)
    app.include_router(parent.router)
    app.include_router(kiosk.router)
    app.include_router(events.router)
    return app


app = create_app()
