"""Seed Phase 1 data: two children and the morning + bedtime routines (spec §13).

Idempotent — safe to re-run. Routines are seeded in code here rather than raw SQL
so the same helpers can be reused by tests.
"""

from __future__ import annotations

from sqlite3 import Connection

from app.config import load_config
from app.db import main_db
from app.db.instance_db import instance_conn
from app.services.ids import new_id

CHILDREN = [
    # name, colour, avatar, birth_year, display_mode
    ("Ada", "#4f8cff", "🦊", 2017, "ring_numeric"),  # age 8
    ("Sam", "#e5a13a", "🐻", 2020, "ring"),           # age 5
]

ROUTINES = {
    "morning": [
        ("Get dressed", "👕"),
        ("Brush teeth", "🪥"),
        ("Hair", "💇"),
        ("Shoes on", "👟"),
    ],
    "bedtime": [
        ("Pyjamas on", "🩳"),
        ("Brush teeth", "🪥"),
        ("Tidy up", "🧸"),
    ],
}


def seed(conn: Connection) -> None:
    for name, colour, avatar, birth_year, mode in CHILDREN:
        exists = conn.execute("SELECT 1 FROM children WHERE name = ?", (name,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO children (id, name, colour, avatar, birth_year, "
                "display_mode, sort_order, active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (new_id(), name, colour, avatar, birth_year, mode, len(name)),
            )

    for rname, steps in ROUTINES.items():
        row = conn.execute("SELECT id FROM routines WHERE name = ?", (rname,)).fetchone()
        if row:
            continue
        rid = new_id()
        conn.execute(
            "INSERT INTO routines (id, name, kind, active) VALUES (?, ?, ?, 1)",
            (rid, rname, rname),
        )
        for pos, (title, icon) in enumerate(steps):
            conn.execute(
                "INSERT INTO steps (id, routine_id, position, title, icon, kind, "
                "floor_seconds, active) VALUES (?, ?, ?, ?, ?, 'task', 30, 1)",
                (new_id(), rid, pos, title, icon),
            )


def main() -> None:
    config = load_config()
    main_db.bootstrap(config)
    with instance_conn(config) as conn:
        seed(conn)
    print("Seeded children and routines.")


if __name__ == "__main__":
    main()
