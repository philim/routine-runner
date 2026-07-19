from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.db import main_db
from app.db.instance_db import instance_conn
from app.main import create_app
from app.routes.deps import COOKIE_NAME
from app.services import device_service
from scripts.seed_routines import seed


@pytest.fixture
def config(tmp_path) -> Config:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Config(
        data_dir=data_dir,
        main_db_path=data_dir / "main.db",
        household_id="test",
        household_name="Test",
        jwt_secret="test-secret",
        token_ttl_days=1,
        enrol_ttl_seconds=300,
        ntfy_topic=None,
        notify_backend="noop",
    )


@pytest.fixture
def seeded(config: Config) -> Config:
    main_db.bootstrap(config)
    with instance_conn(config) as conn:
        seed(conn)
    return config


@pytest.fixture
def app(seeded: Config):
    application = create_app()
    # override lifespan-managed config with the test config
    application.state.config = seeded
    return application


@pytest.fixture
def parent_token(seeded: Config) -> str:
    raw = device_service.create_enrolment_token(seeded, "parent", None)
    return device_service.claim(seeded, raw, "Test Parent").jwt


@pytest.fixture
def kiosk_token(seeded: Config) -> str:
    raw = device_service.create_enrolment_token(seeded, "kiosk", None)
    return device_service.claim(seeded, raw, "Hallway Kiosk").jwt


@pytest.fixture
def parent_client(app, parent_token: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, parent_token)
    return client


@pytest.fixture
def kiosk_client(app, kiosk_token: str) -> TestClient:
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, kiosk_token)
    return client


@pytest.fixture
def children(seeded: Config):
    with instance_conn(seeded) as conn:
        rows = conn.execute("SELECT * FROM children ORDER BY sort_order").fetchall()
        return [dict(r) for r in rows]


@pytest.fixture
def morning_routine_id(seeded: Config) -> str:
    with instance_conn(seeded) as conn:
        row = conn.execute("SELECT id FROM routines WHERE name = 'morning'").fetchone()
        return row["id"]


@pytest.fixture
def bedtime_routine_id(seeded: Config) -> str:
    """The bedtime routine is task-only (no gate) — handy for Phase 1 flow tests."""
    with instance_conn(seeded) as conn:
        row = conn.execute("SELECT id FROM routines WHERE name = 'bedtime'").fetchone()
        return row["id"]
