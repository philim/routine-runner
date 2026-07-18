from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row


@dataclass
class Child:
    id: str
    name: str
    colour: str
    avatar: str | None
    nfc_token: str | None
    birth_year: int | None
    display_mode: str  # ring | ring_numeric
    sort_order: int
    active: int

    @classmethod
    def from_row(cls, row: Row) -> Child:
        return cls(
            id=row["id"],
            name=row["name"],
            colour=row["colour"],
            avatar=row["avatar"],
            nfc_token=row["nfc_token"],
            birth_year=row["birth_year"],
            display_mode=row["display_mode"],
            sort_order=row["sort_order"],
            active=row["active"],
        )
