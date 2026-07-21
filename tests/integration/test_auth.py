"""Auth and kiosk-scope enforcement (spec §9.2, §9.3)."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.db import main_db
from app.routes.deps import COOKIE_KIOSK, COOKIE_PARENT
from app.services import device_service


def test_kiosk_cannot_reach_parent_routes(app, parent_token, kiosk_token):
    # Parent must exist so this is scope enforcement, not recovery redirect.
    client = TestClient(app)
    client.cookies.set(COOKIE_KIOSK, kiosk_token)
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert home.headers["location"] == "/kiosk"
    devices = client.get("/devices", follow_redirects=False)
    assert devices.status_code == 303
    assert devices.headers["location"] == "/kiosk"


def test_parent_cannot_reach_kiosk_routes(app, parent_token):
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    assert client.get("/kiosk").status_code == 403


def test_parent_and_kiosk_cookies_coexist(app, parent_token, kiosk_token):
    """Same browser can hold both sessions (two tabs on one Mac)."""
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    client.cookies.set(COOKIE_KIOSK, kiosk_token)
    assert client.get("/").status_code == 200
    assert client.get("/devices").status_code == 200
    assert client.get("/kiosk").status_code == 200


def test_devices_uses_parent_when_both_cookies_present(app, parent_token, kiosk_token):
    """/devices enters parent mode even if a kiosk session shares the browser."""
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    client.cookies.set(COOKIE_KIOSK, kiosk_token)
    page = client.get("/devices")
    assert page.status_code == 200
    assert "Devices" in page.text


def test_stale_revoked_parent_cookie_falls_back_to_legacy(app, seeded):
    """A revoked rr_parent must not block a still-valid legacy parent JWT."""
    first = device_service.claim(
        seeded, device_service.create_enrolment_token(seeded, "parent", None), "First"
    )
    second = device_service.claim(
        seeded, device_service.create_enrolment_token(seeded, "parent", None), "Second"
    )
    device_service.revoke(seeded, second.device.id)

    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, second.jwt)  # revoked
    client.cookies.set("rr_token", first.jwt)  # still valid
    assert client.get("/devices").status_code == 200


def test_revoking_own_device_clears_parent_cookie(app, seeded, parent_token):
    other = device_service.claim(
        seeded, device_service.create_enrolment_token(seeded, "parent", None), "Other"
    )
    self_id = next(
        d.id for d in device_service.list_devices(seeded)
        if d.role == "parent" and d.revoked_at is None and d.id != other.device.id
    )
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    response = client.post(f"/devices/{self_id}/revoke", headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert response.headers.get("HX-Redirect") == "/"
    set_cookie = response.headers.get("set-cookie", "")
    assert "rr_parent=" in set_cookie.lower()
    # Revoked JWT no longer authorizes parent mode.
    assert client.get("/devices").status_code == 403


def test_no_token_redirects_to_setup_when_unclaimed(app):
    client = TestClient(app)
    for path in ("/", "/kiosk"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/setup"


def test_no_token_is_rejected_when_devices_exist(app, parent_token):
    client = TestClient(app)
    assert client.get("/").status_code == 403
    assert client.get("/kiosk").status_code == 403


def test_revocation_is_immediate(app, seeded, parent_token):
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    assert client.get("/").status_code == 200
    # revoke every device — household is unclaimed again, so /setup reopens
    for d in device_service.list_devices(seeded):
        device_service.revoke(seeded, d.id)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_kiosk_only_household_reopens_setup(app, seeded, parent_token, kiosk_token):
    """Revoking the last parent must not brick the app behind a surviving kiosk."""
    parent_id = next(
        d.id for d in device_service.list_devices(seeded)
        if d.role == "parent" and d.revoked_at is None
    )
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)

    response = client.post(f"/devices/{parent_id}/revoke", headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/setup"
    assert not main_db.has_active_parents(seeded.main_db_path)
    assert main_db.has_devices(seeded.main_db_path)  # kiosk still live

    # Setup is reachable again; kiosk session still works.
    assert client.get("/setup").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 303

    kiosk_client = TestClient(app)
    kiosk_client.cookies.set(COOKIE_KIOSK, kiosk_token)
    assert kiosk_client.get("/kiosk").status_code == 200


def test_setup_claim_recovers_kiosk_only_household(app, seeded, kiosk_token):
    assert not main_db.has_active_parents(seeded.main_db_path)
    client = TestClient(app)
    page = client.get("/setup")
    assert page.status_code == 200
    match = re.search(r'name="token" value="([^"]+)"', page.text)
    assert match
    claimed = client.post(
        "/setup/claim", data={"token": match.group(1), "label": "Recovery phone"}
    )
    assert claimed.status_code == 200
    assert main_db.has_active_parents(seeded.main_db_path)
    assert client.get("/").status_code == 200

