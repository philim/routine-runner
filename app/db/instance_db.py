"""Connection context manager for the per-household instance database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection

from app.config import Config
from app.db import connect


@contextmanager
def instance_conn(config: Config) -> Iterator[Connection]:
    with connect(config.instance_db_path) as conn:
        yield conn
