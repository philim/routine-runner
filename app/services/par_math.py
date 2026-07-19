"""Pure par-calibration math (spec §5.2–5.4).

Kept dependency-free and side-effect-free so it can be exhaustively unit tested
against the worked examples in the design spec.
"""

from __future__ import annotations

GRACE_FLOOR_SECONDS = 30
GRACE_FRACTION = 0.20
HARD_FLOOR_SECONDS = 60
WEEKLY_MOVE_CAP = 0.10


def grace(baseline_seconds: float) -> int:
    """grace = max(30s, 0.20 × baseline) (§5.3)."""
    return int(round(max(GRACE_FLOOR_SECONDS, GRACE_FRACTION * baseline_seconds)))


def median(values: list[int]) -> float:
    """Median of a non-empty list (§5.4 — resists the one disaster morning)."""
    if not values:
        raise ValueError("median of empty sequence")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2


def apply_floor(par_seconds: float, step_floor_seconds: int | None) -> int:
    """Hard floor: par >= max(step.floor_seconds, 60s) (§5.4 rule 4)."""
    floor = max(step_floor_seconds or 0, HARD_FLOOR_SECONDS)
    return int(round(max(par_seconds, floor)))


def apply_weekly_cap(new_par: float, previous_par: int, cap: float = WEEKLY_MOVE_CAP) -> float:
    """Clamp movement to ±cap of the previous par (§5.4 rule 2)."""
    lo = previous_par * (1 - cap)
    hi = previous_par * (1 + cap)
    return min(max(new_par, lo), hi)


def ramp_par(samples: list[int], step_floor_seconds: int | None) -> tuple[int, str] | None:
    """Compute the bootstrap-ramp par from a step's chronological samples (§5.2).

    ``samples`` are elapsed seconds of completed, non-excluded segments for one
    (child, step), oldest first. Sample count doubles as the run number so a
    step added later ramps independently (§16 Q1).

    Returns ``(par_seconds, basis)`` or ``None`` when there is no par yet
    (run 1 — the measurement run).
    """
    n = len(samples)
    if n == 0:
        return None  # run 1: no par
    if n < 4:
        baseline = float(min(samples))  # runs 2–4: best-so-far + grace
        basis = "best"
    else:
        baseline = median(samples[-10:])  # run 5+: median(last 10) + grace
        basis = "median"
    par = baseline + grace(baseline)
    return apply_floor(par, step_floor_seconds), basis


def steady_par(
    samples: list[int], step_floor_seconds: int | None, previous_par: int | None
) -> int | None:
    """Weekly steady-state recompute (§5.4): median(last 10) + grace, capped, floored."""
    if len(samples) < 4:
        return None
    baseline = median(samples[-10:])
    par = baseline + grace(baseline)
    if previous_par is not None:
        par = apply_weekly_cap(par, previous_par)
    return apply_floor(par, step_floor_seconds)
