"""FastAPI application entry point (spec §8)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.db import main_db
from app.routes import enrol, events, kiosk, parent

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    main_db.bootstrap(config)
    app.state.config = config
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Routine Runner", lifespan=lifespan)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(enrol.router)
    app.include_router(parent.router)
    app.include_router(kiosk.router)
    app.include_router(events.router)
    return app


app = create_app()
