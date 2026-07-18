"""Idempotent migration runner.

Applies numbered ``.sql`` files in order and records the applied version.
Safe to run on every boot (technical plan §3.2).
"""

from __future__ import annotations

from pathlib import Path

from app.db import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _sql_files(subdir: str) -> list[Path]:
    d = MIGRATIONS_DIR / subdir
    return sorted(d.glob("*.sql"))


def migrate_main(db_path: Path) -> int:
    """Apply main.db migrations. Returns the resulting version."""
    with connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at INTEGER)"
        )
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        current = row["v"] or 0
        applied = current
        for f in _sql_files("main"):
            version = int(f.stem.split("_", 1)[0])
            if version > current:
                conn.executescript(f.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, 0)",
                    (version,),
                )
                applied = version
        return applied


def migrate_instance(db_path: Path) -> int:
    """Apply household instance migrations. Returns the resulting version."""
    with connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at INTEGER)"
        )
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        current = row["v"] or 0
        applied = current
        for f in _sql_files("instance"):
            version = int(f.stem.split("_", 1)[0])
            if version > current:
                conn.executescript(f.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, 0)",
                    (version,),
                )
                applied = version
        return applied
