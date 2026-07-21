"""SQLite connection helpers.

All access flows through a context manager that enables foreign keys and WAL,
runs inside a transaction, and commits or rolls back (spec §8.1).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Wait up to 30s on lock contention (kiosk polls + SSE can overlap writes).
_BUSY_TIMEOUT_MS = 30_000


def _connect(path: Path) -> sqlite3.Connection:
    # timeout mirrors busy_timeout so both the Python busy handler and SQLite agree.
    conn = sqlite3.connect(
        path, isolation_level=None, timeout=_BUSY_TIMEOUT_MS / 1000
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def connect(path: Path, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a connection wrapped in a transaction; commit on success.

    Use ``immediate=True`` when the caller intends to write — this acquires the
    reserved lock up front and avoids deferred read→write upgrade deadlocks.
    """
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        # executescript() and other statements may have implicitly committed;
        # only commit if a transaction is still open.
        if conn.in_transaction:
            conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
