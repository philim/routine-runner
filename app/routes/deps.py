"""FastAPI dependencies: config, templates, and role-scoped auth (spec §9.3)."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from fastapi import Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from app.config import Config
from app.db import main_db
from app.models.device import Device
from app.services import device_service

# Role-specific cookies so parent + kiosk sessions can coexist in one browser
# (e.g. two tabs while developing on a Mac). Legacy ``rr_token`` is still read.
COOKIE_PARENT = "rr_parent"
COOKIE_KIOSK = "rr_kiosk"
COOKIE_LEGACY = "rr_token"
COOKIE_NAME = COOKIE_PARENT  # back-compat alias for tests / imports

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_config(request: Request) -> Config:
    return request.app.state.config


def cookie_name_for_role(role: str) -> str:
    if role == "parent":
        return COOKIE_PARENT
    if role == "kiosk":
        return COOKIE_KIOSK
    raise ValueError(f"unknown role: {role}")


def set_device_cookie(response: Response, config: Config, token: str, role: str) -> None:
    """Persist a device JWT without clobbering the other role's session."""
    response.set_cookie(
        cookie_name_for_role(role),
        token,
        httponly=True,
        samesite="lax",
        max_age=config.token_ttl_days * 86400,
    )
    # Drop the old shared cookie so it can't override the role-specific ones.
    response.delete_cookie(COOKIE_LEGACY)


def clear_device_cookie(response: Response, role: str) -> None:
    response.delete_cookie(cookie_name_for_role(role))
    response.delete_cookie(COOKIE_LEGACY)


def _token_for_role(request: Request, role: str) -> str | None:
    token = request.cookies.get(cookie_name_for_role(role))
    if token:
        return token
    return request.cookies.get(COOKIE_LEGACY)


def _device_for_role(request: Request, config: Config, role: str) -> Device | None:
    token = _token_for_role(request, role)
    if not token:
        return None
    device = device_service.validate(config, token)
    if device is None or device.role != role:
        return None
    return device


def _reject_or_redirect_to_setup(config: Config, detail: str) -> NoReturn:
    """Send households with no active parent to /setup; otherwise reject with 403."""
    if not main_db.has_active_parents(config.main_db_path):
        raise HTTPException(status_code=303, headers={"Location": "/setup"})
    raise HTTPException(status_code=403, detail=detail)


def current_device(
    request: Request, config: Config = Depends(get_config)
) -> Device | None:
    """Prefer parent when both cookies are present (dual-tab local testing)."""
    parent = _device_for_role(request, config, "parent")
    if parent is not None:
        return parent
    return _device_for_role(request, config, "kiosk")


def require_parent(
    request: Request, config: Config = Depends(get_config)
) -> Device:
    device = _device_for_role(request, config, "parent")
    if device is None:
        _reject_or_redirect_to_setup(config, "parent device required")
    return device


def require_kiosk(
    request: Request, config: Config = Depends(get_config)
) -> Device:
    device = _device_for_role(request, config, "kiosk")
    if device is None:
        _reject_or_redirect_to_setup(config, "kiosk device required")
    return device
