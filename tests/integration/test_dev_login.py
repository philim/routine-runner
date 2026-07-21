"""Dev-mode auth bypass at /parent/dev-login (gated by RR_DEV_MODE)."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.routes.deps import COOKIE_PARENT
from app.services import device_service


def _token_from_set_cookie(response) -> str:
    set_cookie = response.headers.get("set-cookie", "")
    assert "rr_parent=" in set_cookie.lower()
    return set_cookie.split("rr_parent=", 1)[1].split(";", 1)[0]


def test_dev_login_disabled_returns_404(app, seeded):
    client = TestClient(app)
    response = client.post("/parent/dev-login", follow_redirects=False)
    assert response.status_code == 404


def test_dev_login_creates_and_authorizes_parent(app, seeded):
    app.state.config = replace(seeded, dev_mode=True)
    client = TestClient(app)
    response = client.post("/parent/dev-login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    client.cookies.set(COOKIE_PARENT, _token_from_set_cookie(response))
    assert client.get("/devices").status_code == 200
    assert client.get("/").status_code == 200


def test_dev_login_reuses_existing_parent(app, seeded):
    app.state.config = replace(seeded, dev_mode=True)
    device_service.claim(
        seeded, device_service.create_enrolment_token(seeded, "parent", None), "Phil"
    )
    active_before = [
        d for d in device_service.list_devices(seeded)
        if d.role == "parent" and d.revoked_at is None
    ]
    client = TestClient(app)
    client.post("/parent/dev-login")
    active_after = [
        d for d in device_service.list_devices(seeded)
        if d.role == "parent" and d.revoked_at is None
    ]
    assert len(active_after) == len(active_before)


def test_dev_login_button_only_visible_in_dev_mode(app, seeded):
    client = TestClient(app)
    assert "Dev login" not in client.get("/parent").text
    app.state.config = replace(seeded, dev_mode=True)
    assert "Dev login" in client.get("/parent").text
