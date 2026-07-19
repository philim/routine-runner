"""Star scoring, records, streaks and run bonuses (spec §7, §6.6).

Segment stars are computed at completion time from the snapshotted par. Records,
streaks and run bonuses are settled when a track completes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from sqlite3 import Connection

from app.services import clock


def segment_stars(elapsed_seconds: int | None, par_seconds: int | None) -> int:
    """Stars for a completed timed segment (§7).

    No par (run 1): one participation star. With a par: ``<= par`` -> 2,
    ``<= 1.25 x par`` -> 1, else 0.
    """
    if elapsed_seconds is None:
        return 0
    if par_seconds is None:
        return 1  # run-1 participation star
    if elapsed_seconds <= par_seconds:
        return 2
    if elapsed_seconds <= 1.25 * par_seconds:
        return 1
    return 0


def _routine_id(conn: Connection, run_id: str) -> str:
    return conn.execute("SELECT routine_id FROM runs WHERE id = ?", (run_id,)).fetchone()[
        "routine_id"
    ]


def _upsert_record(
    conn: Connection, child_id: str, routine_id: str, step_id: str | None,
    seconds: int, run_id: str, now: int,
) -> bool:
    """Set a new best if ``seconds`` beats the stored record. Returns True if it did."""
    if step_id is None:
        existing = conn.execute(
            "SELECT best_seconds FROM records WHERE child_id = ? AND routine_id = ? "
            "AND step_id IS NULL",
            (child_id, routine_id),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT best_seconds FROM records WHERE child_id = ? AND routine_id = ? "
            "AND step_id = ?",
            (child_id, routine_id, step_id),
        ).fetchone()
    if existing is not None and existing["best_seconds"] <= seconds:
        return False
    # delete-then-insert keeps the (child, routine, step) record unique without a PK
    conn.execute(
        "DELETE FROM records WHERE child_id = ? AND routine_id = ? "
        "AND ((step_id IS NULL AND ? IS NULL) OR step_id = ?)",
        (child_id, routine_id, step_id, step_id),
    )
    conn.execute(
        "INSERT INTO records (child_id, routine_id, step_id, best_seconds, achieved_at, run_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (child_id, routine_id, step_id, seconds, now, run_id),
    )
    return True


def finalize_track(conn: Connection, run_id: str, child_id: str) -> int:
    """Update records + streak and return the run-bonus stars for this track (§7)."""
    now = clock.now_ms()
    routine_id = _routine_id(conn, run_id)
    segs = conn.execute(
        "SELECT * FROM segments WHERE run_id = ? AND child_id = ? ORDER BY position",
        (run_id, child_id),
    ).fetchall()

    task_done = [s for s in segs if s["kind"] == "task" and s["state"] == "done"]
    parred = [s for s in task_done if s["par_seconds"] is not None]

    # per-step records (exclude rejected redos — atypical two-attempt times)
    for s in task_done:
        if s["rejection_count"] == 0 and s["elapsed_seconds"] is not None:
            _upsert_record(conn, child_id, routine_id, s["step_id"],
                           s["elapsed_seconds"], run_id, now)

    all_completed = all(s["state"] == "done" for s in segs)
    total_seconds = sum(s["elapsed_seconds"] or 0 for s in task_done)

    bonus = 0
    if all_completed and segs:
        bonus += 2  # all steps completed
    if parred and all(s["elapsed_seconds"] <= s["par_seconds"] for s in parred):
        bonus += 3  # all timed steps under par

    # whole-routine total record (+5), only meaningful on a fully completed run
    if all_completed and task_done:
        if _upsert_record(conn, child_id, routine_id, None, total_seconds, run_id, now):
            bonus += 5

    _update_streak(conn, child_id, routine_id, parred, now)
    return bonus


def _update_streak(conn, child_id, routine_id, parred, now_ms) -> None:
    """A run qualifies if every par'd segment cleared its threshold (>=1 star) (§7)."""
    qualifies = bool(parred) and all(s["stars"] >= 1 for s in parred)
    date = datetime.fromtimestamp(now_ms / 1000, UTC).date().isoformat()
    row = conn.execute(
        "SELECT * FROM streaks WHERE child_id = ? AND routine_id = ?",
        (child_id, routine_id),
    ).fetchone()
    if row is None:
        current = 1 if qualifies else 0
        conn.execute(
            "INSERT INTO streaks (child_id, routine_id, current, best, last_qualifying_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (child_id, routine_id, current, current, date if qualifies else None),
        )
        return
    current = row["current"] + 1 if qualifies else 0
    best = max(row["best"], current)
    conn.execute(
        "UPDATE streaks SET current = ?, best = ?, last_qualifying_date = ? "
        "WHERE child_id = ? AND routine_id = ?",
        (current, best, date if qualifies else row["last_qualifying_date"],
         child_id, routine_id),
    )
