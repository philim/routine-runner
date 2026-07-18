"""Foundations: migrations apply cleanly and enrolment works (technical plan §3.5)."""

from __future__ import annotations

from app.db import main_db
from app.db.instance_db import instance_conn
from app.services import device_service


def test_bootstrap_creates_household_and_schema(config):
    main_db.bootstrap(config)
    # idempotent
    main_db.bootstrap(config)
    with instance_conn(config) as conn:
        # a known table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='segments'"
        ).fetchone()
        assert row is not None


def test_enrol_and_validate_device(config):
    main_db.bootstrap(config)
    assert not main_db.has_devices(config.main_db_path)
    raw = device_service.create_enrolment_token(config, "parent", None)
    minted = device_service.claim(config, raw, "Phone")
    assert main_db.has_devices(config.main_db_path)
    device = device_service.validate(config, minted.jwt)
    assert device is not None
    assert device.role == "parent"


def test_enrolment_token_single_use(config):
    main_db.bootstrap(config)
    raw = device_service.create_enrolment_token(config, "kiosk", None)
    device_service.claim(config, raw, "Kiosk")
    try:
        device_service.claim(config, raw, "Kiosk again")
        assert False, "token should be single-use"
    except device_service.EnrolmentError:
        pass
