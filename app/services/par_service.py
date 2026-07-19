"""Par calibration and recompute (spec §5, technical plan §5.2).

The ``pars`` table is append-only: the live par for a (child, step) is the row
with ``superseded_at IS NULL``. Recompute writes a new row and supersedes the
old one, so historical ``segments.par_seconds`` snapshots are never rewritten.
"""

from __future__ import annotations

from sqlite3 import Connection, Row

from app.services import clock, par_math
from app.services.ids import new_id

# --- queries ----------------------------------------------------------------

def live_par(conn: Connection, child_id: str, step_id: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM pars WHERE child_id = ? AND step_id = ? AND superseded_at IS NULL",
        (child_id, step_id),
    ).fetchone()


def par_history(conn: Connection, child_id: str, step_id: str) -> list[Row]:
    return conn.execute(
        "SELECT * FROM pars WHERE child_id = ? AND step_id = ? "
        "ORDER BY effective_from DESC",
        (child_id, step_id),
    ).fetchall()


def step_samples(conn: Connection, child_id: str, step_id: str, limit: int = 10) -> list[int]:
    """Chronological elapsed samples for a step, excluding rejected/skipped/abandoned.

    Rejected redos (rejection_count > 0) are excluded per §5.4/§6.4 — a two-attempt
    time is atypical and would inflate the median.
    """
    rows = conn.execute(
        "SELECT s.elapsed_seconds FROM segments s "
        "JOIN runs r ON r.id = s.run_id "
        "WHERE s.child_id = ? AND s.step_id = ? AND s.kind = 'task' "
        "AND s.state = 'done' AND s.rejection_count = 0 AND s.elapsed_seconds IS NOT NULL "
        "ORDER BY r.started_at ASC",
        (child_id, step_id),
    ).fetchall()
    values = [r["elapsed_seconds"] for r in rows]
    return values[-limit:] if limit else values


def _step_floor(conn: Connection, step_id: str) -> int | None:
    row = conn.execute("SELECT floor_seconds FROM steps WHERE id = ?", (step_id,)).fetchone()
    return row["floor_seconds"] if row else None


# --- snapshot at run open ---------------------------------------------------

def snapshot_for_child(conn: Connection, run_id: str, child_id: str) -> None:
    """Copy each task segment's current live par into the segment (spec §5.7, §10).

    Called at check-in. A NULL par (run 1 / basis none) leaves the segment
    unscored-against-par, yielding a participation star.
    """
    segs = conn.execute(
        "SELECT id, step_id FROM segments WHERE run_id = ? AND child_id = ? AND kind = 'task'",
        (run_id, child_id),
    ).fetchall()
    for seg in segs:
        par = live_par(conn, child_id, seg["step_id"])
        if par is not None:
            conn.execute(
                "UPDATE segments SET par_seconds = ? WHERE id = ?",
                (par["par_seconds"], seg["id"]),
            )


# --- writes -----------------------------------------------------------------

def _write_par(
    conn: Connection, child_id: str, step_id: str, par_seconds: int, basis: str, n: int
) -> None:
    now = clock.now_ms()
    conn.execute(
        "UPDATE pars SET superseded_at = ? WHERE child_id = ? AND step_id = ? "
        "AND superseded_at IS NULL",
        (now, child_id, step_id),
    )
    conn.execute(
        "INSERT INTO pars (id, child_id, step_id, par_seconds, grace_seconds, basis, "
        "effective_from, computed_from_n, frozen, superseded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)",
        (new_id(), child_id, step_id, par_seconds,
         par_math.grace(par_seconds), basis, now, n),
    )


def recompute_ramp_after_run(conn: Connection, run_id: str) -> None:
    """After a run closes, advance each (child, step) bootstrap par (§5.2).

    Frozen pars are left untouched (§5.4 rule 5).
    """
    rows = conn.execute(
        "SELECT DISTINCT child_id, step_id FROM segments "
        "WHERE run_id = ? AND kind = 'task'",
        (run_id,),
    ).fetchall()
    for row in rows:
        child_id, step_id = row["child_id"], row["step_id"]
        current = live_par(conn, child_id, step_id)
        if current is not None and current["frozen"]:
            continue
        samples = step_samples(conn, child_id, step_id)
        result = par_math.ramp_par(samples, _step_floor(conn, step_id))
        if result is None:
            continue
        par_seconds, basis = result
        _write_par(conn, child_id, step_id, par_seconds, basis, len(samples))


def recompute_weekly(conn: Connection) -> int:
    """Steady-state weekly recompute across all (child, step) pairs (§5.4).

    Returns the number of pars updated. Skips frozen pars and steps without
    enough samples. Movement is capped ±10% vs the previous par.
    """
    updated = 0
    pairs = conn.execute(
        "SELECT DISTINCT child_id, step_id FROM segments WHERE kind = 'task'"
    ).fetchall()
    for pair in pairs:
        child_id, step_id = pair["child_id"], pair["step_id"]
        current = live_par(conn, child_id, step_id)
        if current is not None and current["frozen"]:
            continue
        previous = current["par_seconds"] if current else None
        samples = step_samples(conn, child_id, step_id)
        new_par = par_math.steady_par(samples, _step_floor(conn, step_id), previous)
        if new_par is None or new_par == previous:
            continue
        _write_par(conn, child_id, step_id, new_par, "median", len(samples))
        updated += 1
    return updated


def freeze(conn: Connection, par_id: str) -> None:
    """Lock a par so recompute leaves it alone (§5.4 rule 5)."""
    conn.execute("UPDATE pars SET frozen = 1 WHERE id = ?", (par_id,))


def reset(conn: Connection, child_id: str, step_id: str) -> None:
    """Discard par history and restart the §5.2 ramp from run 1 (§5.4 rule 6)."""
    conn.execute(
        "UPDATE pars SET superseded_at = ? WHERE child_id = ? AND step_id = ? "
        "AND superseded_at IS NULL",
        (clock.now_ms(), child_id, step_id),
    )
