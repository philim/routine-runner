"""Bootstrap and device enrolment (spec §9.2, §11).

``/setup`` is reachable only while the device table is empty. Enrolled parents
add further devices via a single-use QR token.
"""

from __future__ import annotations

import base64

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from app.config import Config
from app.db import main_db
from app.models.device import Device
from app.routes.deps import COOKIE_NAME, get_config, require_parent, templates
from app.services import device_service

router = APIRouter()


def _qr_data_uri(text: str) -> str:
    # SVG factory needs no Pillow; embed as a data URI.
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
    svg_bytes = img.to_string()
    b64 = base64.b64encode(svg_bytes).decode()
    return f"data:image/svg+xml;base64,{b64}"


def _set_token_cookie(response: Response, config: Config, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=config.token_ttl_days * 86400,
    )


@router.get("/setup")
def setup(request: Request, config: Config = Depends(get_config)):
    if main_db.has_devices(config.main_db_path):
        raise HTTPException(status_code=404, detail="setup already completed")
    raw = device_service.create_enrolment_token(config, role="parent", created_by=None)
    return templates.TemplateResponse(
        request, "parent/setup.html", {"token": raw, "qr": _qr_data_uri(raw)}
    )


@router.post("/setup/claim")
def setup_claim(
    request: Request,
    token: str = Form(...),
    label: str = Form(...),
    config: Config = Depends(get_config),
):
    if main_db.has_devices(config.main_db_path):
        raise HTTPException(status_code=404, detail="setup already completed")
    minted = device_service.claim(config, token, label)
    response = templates.TemplateResponse(
        request, "parent/enrolled.html", {"device": minted.device}
    )
    _set_token_cookie(response, config, minted.jwt)
    return response


@router.post("/devices/enrol")
def enrol_device(
    request: Request,
    role: str = Form(...),
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    raw = device_service.create_enrolment_token(config, role=role, created_by=parent.id)
    return templates.TemplateResponse(
        request, "partials/enrol_qr.html",
        {"token": raw, "role": role, "qr": _qr_data_uri(raw)}
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
    config: Config = Depends(get_config),
):
    minted = device_service.claim(config, token, label)
    response = templates.TemplateResponse(
        request, "parent/enrolled.html", {"device": minted.device}
    )
    _set_token_cookie(response, config, minted.jwt)
    return response


@router.get("/devices")
def devices(
    request: Request,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    return templates.TemplateResponse(
        request, "parent/devices.html",
        {"devices": device_service.list_devices(config)}
    )


@router.post("/devices/{device_id}/revoke")
def revoke_device(
    device_id: str,
    parent: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    device_service.revoke(config, device_id)
    return Response(status_code=204)
