"""Config loading is robust to blank/whitespace .env values."""

from __future__ import annotations

import pytest

from app.config import INSECURE_JWT_SECRET, load_config


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch, tmp_path):
    # Isolate from the developer's real environment / .env.
    for key in list(__import__("os").environ):
        if key.startswith("RR_") or key in ("RESEND_API_KEY",):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RR_DATA_DIR", str(tmp_path))


def test_blank_jwt_secret_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RR_JWT_SECRET", "")  # e.g. `RR_JWT_SECRET=` in .env
    config = load_config()
    assert config.jwt_secret == INSECURE_JWT_SECRET


def test_set_jwt_secret_is_respected(monkeypatch):
    monkeypatch.setenv("RR_JWT_SECRET", "  strong-secret  ")
    config = load_config()
    assert config.jwt_secret == "strong-secret"


def test_blank_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RR_TOKEN_TTL_DAYS", "")
    config = load_config()
    assert config.token_ttl_days == 365


def test_blank_optional_becomes_none(monkeypatch):
    monkeypatch.setenv("RR_HOSTNAME", "")
    monkeypatch.setenv("RR_NTFY_TOPIC", "")
    config = load_config()
    assert config.hostname is None
    assert config.ntfy_topic is None


def test_email_backend_defaults_noop_without_key(monkeypatch):
    config = load_config()
    assert config.email_backend == "noop"
    assert config.resend_api_key is None


def test_email_backend_defaults_resend_with_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    config = load_config()
    assert config.email_backend == "resend"
    assert config.resend_api_key == "re_test"
