"""SQLite connection helpers.

All access flows through a context manager that enables foreign keys and WAL,
runs inside a transaction, and commits or rolls back (spec §8.1).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit off via explicit txn
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection wrapped in a transaction; commit on success."""
    conn = _connect(path)
    try:
        conn.execute("BEGIN")
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
