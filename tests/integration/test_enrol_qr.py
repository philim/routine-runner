"""Enrolment QR must encode an absolute claim URL (Android camera requires it)."""

from __future__ import annotations

import re
from dataclasses import replace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.routes.deps import COOKIE_PARENT
from app.routes.enrol import _claim_url, _qr_data_uri


def test_claim_url_prefers_configured_hostname(seeded):
    config = replace(seeded, hostname="routines.example.test")
    request = MagicMock()
    request.base_url = "http://ignored.invalid/"
    assert (
        _claim_url(request, config, "tok123")
        == "https://routines.example.test/claim?token=tok123"
    )


def test_claim_url_falls_back_to_request_origin(seeded):
    request = MagicMock()
    request.base_url = "http://127.0.0.1:8000/"
    assert (
        _claim_url(request, seeded, "tok123")
        == "http://127.0.0.1:8000/claim?token=tok123"
    )


def test_enrol_qr_encodes_absolute_claim_url(app, parent_token, seeded):
    app.state.config = replace(seeded, hostname="routines.example.test")
    client = TestClient(app)
    client.cookies.set(COOKIE_PARENT, parent_token)

    response = client.post("/devices/enrol", data={"role": "kiosk"})
    assert response.status_code == 200
    body = response.text

    match = re.search(
        r"https://routines\.example\.test/claim\?token=([A-Za-z0-9_\-]+)", body
    )
    assert match, "expected absolute claim URL in enrol QR partial"
    claim_url = match.group(0)
    assert _qr_data_uri(claim_url) in body
