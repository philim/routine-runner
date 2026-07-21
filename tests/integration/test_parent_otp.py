"""Parent email OTP login at /parent."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.routes.deps import COOKIE_PARENT
from app.services import device_service, email_service, otp_service


def _otp_from_outbox() -> str:
    assert email_service.outbox()
    match = re.search(r"\b(\d{6})\b", email_service.outbox()[-1]["text"])
    assert match
    return match.group(1)


def test_parent_otp_login_sets_parent_cookie(app, seeded):
    email_service.clear_outbox()
    device_service.claim(
        seeded,
        device_service.create_enrolment_token(seeded, "parent", None),
        "Phil",
        email="phil@example.com",
    )
    client = TestClient(app)

    page = client.get("/parent")
    assert page.status_code == 200
    assert "Parent login" in page.text

    sent = client.post("/parent/otp", data={"email": "phil@example.com"})
    assert sent.status_code == 200
    assert email_service.outbox()[0]["to"] == "phil@example.com"
    code = _otp_from_outbox()

    verified = client.post(
        "/parent/verify",
        data={"email": "phil@example.com", "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303
    assert verified.headers["location"] == "/"
    set_cookie = verified.headers.get("set-cookie", "")
    assert "rr_parent=" in set_cookie.lower()

    # Use the session cookie for parent routes.
    token = set_cookie.split("rr_parent=", 1)[1].split(";", 1)[0]
    client.cookies.set(COOKIE_PARENT, token)
    assert client.get("/devices").status_code == 200
    assert client.get("/").status_code == 200


def test_parent_otp_unknown_email_does_not_leak(app, seeded):
    email_service.clear_outbox()
    device_service.claim(
        seeded,
        device_service.create_enrolment_token(seeded, "parent", None),
        "Phil",
        email="phil@example.com",
    )
    client = TestClient(app)
    response = client.post("/parent/otp", data={"email": "stranger@example.com"})
    assert response.status_code == 200
    assert "registered" in response.text.lower()
    assert email_service.outbox() == []


def test_parent_otp_wrong_code_rejected(app, seeded):
    email_service.clear_outbox()
    device_service.claim(
        seeded,
        device_service.create_enrolment_token(seeded, "parent", None),
        "Phil",
        email="phil@example.com",
    )
    client = TestClient(app)
    client.post("/parent/otp", data={"email": "phil@example.com"})
    bad = client.post(
        "/parent/verify",
        data={"email": "phil@example.com", "code": "000000"},
    )
    assert bad.status_code == 400
    assert "Incorrect" in bad.text


def test_set_email_enables_otp_login(app, parent_token, seeded):
    email_service.clear_outbox()
    parent = next(d for d in device_service.list_devices(seeded) if d.role == "parent")
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    saved = client.post(
        f"/devices/{parent.id}/email",
        data={"email": "mum@example.com"},
        follow_redirects=False,
    )
    assert saved.status_code in (200, 303)

    anon = TestClient(app)
    anon.post("/parent/otp", data={"email": "mum@example.com"})
    code = _otp_from_outbox()
    device = otp_service.verify_otp(seeded, "mum@example.com", code)
    assert device.id == parent.id


def test_authed_parent_skips_login_form(app, parent_token):
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)
    response = client.get("/parent", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
