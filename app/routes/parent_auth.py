"""Parent email OTP login at ``/parent``."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import Config
from app.models.device import Device
from app.routes.deps import (
    current_device,
    get_config,
    set_device_cookie,
    templates,
)
from app.services import device_service, otp_service

log = logging.getLogger("routine_runner.parent_auth")

router = APIRouter()


@router.get("/parent")
def parent_login(
    request: Request,
    device: Device | None = Depends(current_device),
    config: Config = Depends(get_config),
):
    """Email OTP entry. Already-authed parents go straight to the dashboard."""
    if device is not None and device.role == "parent":
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "parent/login.html",
        {"error": None, "email": "", "step": "email", "dev_mode": config.dev_mode},
    )


@router.post("/parent/dev-login")
def parent_dev_login(
    request: Request,
    config: Config = Depends(get_config),
):
    """DEV ONLY: mint a parent session with no credentials. 404 in production."""
    if not config.dev_mode:
        raise HTTPException(status_code=404)
    device = device_service.get_or_create_dev_parent(config)
    log.warning("DEV LOGIN: minted parent session for device %s (auth bypass)", device.id)
    token = device_service.mint_session(config, device)
    response = RedirectResponse("/", status_code=303)
    set_device_cookie(response, config, token, "parent", request)
    return response


@router.post("/parent/otp")
def parent_request_otp(
    request: Request,
    email: str = Form(...),
    config: Config = Depends(get_config),
):
    error: str | None = None
    try:
        otp_service.request_otp(config, email)
    except otp_service.OtpError as exc:
        error = str(exc)
        return templates.TemplateResponse(
            request,
            "parent/login.html",
            {"error": error, "email": email, "step": "email", "dev_mode": config.dev_mode},
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "parent/login.html",
        {
            "error": None,
            "email": otp_service.normalize_email(email),
            "step": "code",
            "info": "If that email is registered to a parent device, we sent a code.",
        },
    )


@router.post("/parent/verify")
def parent_verify_otp(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    config: Config = Depends(get_config),
):
    try:
        device = otp_service.verify_otp(config, email, code)
    except otp_service.OtpError as exc:
        return templates.TemplateResponse(
            request,
            "parent/login.html",
            {
                "error": str(exc),
                "email": otp_service.normalize_email(email),
                "step": "code",
            },
            status_code=400,
        )
    token = device_service.mint_session(config, device)
    response = RedirectResponse("/", status_code=303)
    set_device_cookie(response, config, token, "parent", request)
    return response
