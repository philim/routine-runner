"""Star scoring (spec §7).

Phase 1 only exercises the run-1 participation path (no pars yet). The par-based
thresholds are implemented here too so Phase 2 wires them in without a rewrite.
"""

from __future__ import annotations


def segment_stars(elapsed_seconds: int | None, par_seconds: int | None) -> int:
    """Stars for a completed timed segment.

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
