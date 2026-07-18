"""Connections and bootstrap for main.db (households, devices, registry)."""

from __future__ import annotations

from pathlib import Path

from app.config import Config
from app.db import connect
from app.db.migrate import migrate_instance, migrate_main
from app.services import clock


def bootstrap(config: Config) -> None:
    """Run migrations and ensure the household + instance rows exist."""
    migrate_main(config.main_db_path)
    migrate_instance(config.instance_db_path)

    with connect(config.main_db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM households WHERE id = ?", (config.household_id,)
        ).fetchone()
        if not exists:
            now = clock.now_ms()
            conn.execute(
                "INSERT INTO households (id, name, created_at) VALUES (?, ?, ?)",
                (config.household_id, config.household_name, now),
            )
            conn.execute(
                "INSERT INTO instances (household_id, db_path, schema_version) "
                "VALUES (?, ?, 1)",
                (config.household_id, str(config.instance_db_path)),
            )


def has_devices(main_db_path: Path) -> bool:
    with connect(main_db_path) as conn:
        row = conn.execute("SELECT 1 FROM devices WHERE revoked_at IS NULL LIMIT 1").fetchone()
        return row is not None
