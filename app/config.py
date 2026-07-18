"""Application configuration, sourced from environment with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    main_db_path: Path
    household_id: str
    household_name: str
    jwt_secret: str
    token_ttl_days: int
    enrol_ttl_seconds: int
    ntfy_topic: str | None
    notify_backend: str  # ntfy | noop

    @property
    def instance_db_path(self) -> Path:
        return self.data_dir / f"household_{self.household_id}.db"


def load_config() -> Config:
    data_dir = Path(os.environ.get("RR_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        data_dir=data_dir,
        main_db_path=data_dir / "main.db",
        household_id=os.environ.get("RR_HOUSEHOLD_ID", "home"),
        household_name=os.environ.get("RR_HOUSEHOLD_NAME", "Home"),
        jwt_secret=os.environ.get("RR_JWT_SECRET", "dev-insecure-change-me"),
        token_ttl_days=int(os.environ.get("RR_TOKEN_TTL_DAYS", "365")),
        enrol_ttl_seconds=int(os.environ.get("RR_ENROL_TTL_SECONDS", "300")),
        ntfy_topic=os.environ.get("RR_NTFY_TOPIC"),
        notify_backend=os.environ.get("RR_NOTIFY_BACKEND", "noop"),
    )
