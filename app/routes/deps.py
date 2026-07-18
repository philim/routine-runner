"""FastAPI dependencies: config, templates, and role-scoped auth (spec §9.3)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.config import Config
from app.models.device import Device
from app.services import device_service

COOKIE_NAME = "rr_token"

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_config(request: Request) -> Config:
    return request.app.state.config


def _device_from_cookie(request: Request, config: Config) -> Device | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return device_service.validate(config, token)


def current_device(
    request: Request, config: Config = Depends(get_config)
) -> Device | None:
    return _device_from_cookie(request, config)


def require_parent(
    request: Request, config: Config = Depends(get_config)
) -> Device:
    device = _device_from_cookie(request, config)
    if device is None or device.role != "parent":
        raise HTTPException(status_code=403, detail="parent device required")
    return device


def require_kiosk(
    request: Request, config: Config = Depends(get_config)
) -> Device:
    device = _device_from_cookie(request, config)
    if device is None or device.role != "kiosk":
        raise HTTPException(status_code=403, detail="kiosk device required")
    return device
