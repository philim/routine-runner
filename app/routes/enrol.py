"""Bootstrap and device enrolment (spec §9.2, §11).

``/setup`` is reachable while no active parent device remains (including
kiosk-only recovery). Enrolled parents add further devices via a single-use QR
token.
"""

from __future__ import annotations

import base64
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.config import Config
from app.db import main_db
from app.models.device import Device
from app.routes.deps import (
    clear_device_cookie,
    current_device,
    get_config,
    require_parent,
    scrub_stale_role_cookies,
    set_device_cookie,
    templates,
)
from app.services import device_service

router = APIRouter()


class _SvgQR(qrcode.image.svg.SvgPathImage):
    """Black modules on an opaque white plate — readable on dark UI backgrounds."""

    background = "#ffffff"


def _qr_data_uri(text: str) -> str:
    # SVG factory needs no Pillow; embed as a data URI.
    img = qrcode.make(text, image_factory=_SvgQR)
    svg_bytes = img.to_string()
    b64 = base64.b64encode(svg_bytes).decode()
    return f"data:image/svg+xml;base64,{b64}"


def _claim_url(request: Request, config: Config, token: str) -> str:
    """Absolute claim URL for QR scanners (Android needs a real http(s) link)."""
    if config.hostname:
        base = f"https://{config.hostname}"
    else:
        base = str(request.base_url).rstrip("/")
    return f"{base}/claim?{urlencode({'token': token})}"


@router.get("/setup")
def setup(request: Request, config: Config = Depends(get_config)):
    # Open while no parent remains — including kiosk-only recovery after the
    # last parent was revoked (spec §9.2 bootstrap).
    if main_db.has_active_parents(config.main_db_path):
        raise HTTPException(status_code=404, detail="setup already completed")
    raw = device_service.create_enrolment_token(config, role="parent", created_by=None)
    claim_url = _claim_url(request, config, raw)
    return templates.TemplateResponse(
        request,
        "parent/setup.html",
        {"token": raw, "claim_url": claim_url, "qr": _qr_data_uri(claim_url)},
    )


@router.post("/setup/claim")
def setup_claim(
    request: Request,
    token: str = Form(...),
    label: str = Form(...),
    email: str = Form(default=""),
    config: Config = Depends(get_config),
):
    if main_db.has_active_parents(config.main_db_path):
        raise HTTPException(status_code=404, detail="setup already completed")
    try:
        minted = device_service.claim(config, token, label, email=email or None)
    except device_service.EnrolmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = templates.TemplateResponse(
        request, "parent/enrolled.html", {"device": minted.device}
    )
    set_device_cookie(response, config, minted.jwt, minted.device.role, request)
    return response


@router.post("/devices/enrol")
def enrol_device(
    request: Request,
    role: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    raw = device_service.create_enrolment_token(config, role=role, created_by=parent.id)
    claim_url = _claim_url(request, config, raw)
    return templates.TemplateResponse(
        request,
        "partials/enrol_qr.html",
        {
            "token": raw,
            "role": role,
            "claim_url": claim_url,
            "qr": _qr_data_uri(claim_url),
        },
    )


@router.get("/claim")
def claim_page(request: Request, token: str, config: Config = Depends(get_config)):
    return templates.TemplateResponse(
        request, "parent/claim.html", {"token": token}
    )


@router.post("/claim")
def claim_submit(
    request: Request,
    token: str = Form(...),
    label: str = Form(...),
    email: str = Form(default=""),
    config: Config = Depends(get_config),
):
    try:
        minted = device_service.claim(config, token, label, email=email or None)
    except device_service.EnrolmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = templates.TemplateResponse(
        request, "parent/enrolled.html", {"device": minted.device}
    )
    set_device_cookie(response, config, minted.jwt, minted.device.role, request)
    return response


@router.get("/devices")
def devices(
    request: Request,
    response: Response,
    device: Device | None = Depends(current_device),
    config: Config = Depends(get_config),
):
    """Parent device manager; kiosk sessions are sent to /kiosk (like ``/``)."""
    scrub_stale_role_cookies(request, response, config)
    if device is None:
        if not main_db.has_active_parents(config.main_db_path):
            return RedirectResponse("/setup", status_code=303)
        raise HTTPException(status_code=403, detail="parent device required")
    if device.role == "kiosk":
        return RedirectResponse("/kiosk", status_code=303)
    if device.role != "parent":
        raise HTTPException(status_code=403, detail="parent device required")

    return templates.TemplateResponse(
        request, "parent/devices.html",
        {"devices": device_service.list_devices(config)}
    )


@router.post("/devices/{device_id}/email")
def set_device_email(
    request: Request,
    device_id: str,
    email: str = Form(default=""),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    del parent  # auth only
    try:
        device_service.set_email(config, device_id, email or None)
    except device_service.EnrolmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "parent/devices.html",
            {"devices": device_service.list_devices(config)},
        )
    return RedirectResponse("/devices", status_code=303)


@router.post("/devices/{device_id}/revoke")
def revoke_device(
    request: Request,
    device_id: str,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    device_service.revoke(config, device_id)
    clearing_self = device_id == parent.id

    if main_db.has_active_parents(config.main_db_path):
        response = Response(status_code=204)
        if clearing_self:
            # Don't leave a revoked JWT in rr_parent — it blocks parent mode.
            clear_device_cookie(response, "parent")
            if request.headers.get("HX-Request"):
                response.headers["HX-Redirect"] = "/"
        return response

    # Last parent gone — reopen bootstrap and drop the now-dead session cookie.
    if request.headers.get("HX-Request"):
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = "/setup"
    else:
        response = Response(status_code=303, headers={"Location": "/setup"})
    clear_device_cookie(response, "parent")
    return response

