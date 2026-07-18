"""Auth and kiosk-scope enforcement (spec §9.2, §9.3)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.routes.deps import COOKIE_NAME
from app.services import device_service


def test_kiosk_cannot_reach_parent_routes(app, kiosk_token):
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, kiosk_token)
    assert client.get("/").status_code == 403
    assert client.get("/devices").status_code == 403


def test_parent_cannot_reach_kiosk_routes(app, parent_token):
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, parent_token)
    assert client.get("/kiosk").status_code == 403


def test_no_token_is_rejected(app):
    client = TestClient(app)
    assert client.get("/").status_code == 403
    assert client.get("/kiosk").status_code == 403


def test_revocation_is_immediate(app, seeded, parent_token):
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, parent_token)
    assert client.get("/").status_code == 200
    # revoke every device, then the same token must fail
    for d in device_service.list_devices(seeded):
        device_service.revoke(seeded, d.id)
    assert client.get("/").status_code == 403
