"""Par math against the worked examples in the design spec (§5.2–5.4)."""

import pytest

from app.services import par_math


def test_grace_floor_and_fraction():
    # §5.3: max(30, 0.20*baseline)
    assert par_math.grace(20) == 30       # 0.2*20=4 -> floor 30
    assert par_math.grace(120) == 30      # 0.2*120=24 -> floor 30
    assert par_math.grace(360) == 72      # 0.2*360=72 -> fraction wins


def test_median_odd_even():
    assert par_math.median([10]) == 10
    assert par_math.median([30, 10, 20]) == 20
    assert par_math.median([10, 20, 30, 40]) == 25


def test_apply_floor_hard_60():
    assert par_math.apply_floor(10, None) == 60
    assert par_math.apply_floor(10, 90) == 90   # step floor wins
    assert par_math.apply_floor(200, 90) == 200


def test_weekly_cap_limits_movement():
    assert par_math.apply_weekly_cap(200, 100) == pytest.approx(110)   # +10% cap
    assert par_math.apply_weekly_cap(50, 100) == pytest.approx(90)     # -10% cap
    assert par_math.apply_weekly_cap(105, 100) == pytest.approx(105)   # within band


def test_ramp_run1_has_no_par():
    assert par_math.ramp_par([], 30) is None


def test_ramp_runs_2_to_4_use_best():
    # after 1 completed sample -> par for run 2 = best + grace
    par, basis = par_math.ramp_par([200], 30)
    assert basis == "best"
    assert par == 200 + par_math.grace(200)   # 200 + 40 = 240
    # best-so-far across 3 samples
    par, basis = par_math.ramp_par([200, 300, 240], 30)
    assert basis == "best"
    assert par == 200 + par_math.grace(200)


def test_ramp_run5_switches_to_median():
    samples = [100, 120, 110, 130]   # 4 samples -> run 5 basis
    par, basis = par_math.ramp_par(samples, 30)
    assert basis == "median"
    baseline = par_math.median(samples)
    assert par == int(round(baseline + par_math.grace(baseline)))


def test_ramp_handover_loosens_vs_best():
    # median-of-4 >= best-of-4, so par at run 5 typically loosens (§5.2 note)
    samples = [100, 120, 110, 130]
    best_par = 100 + par_math.grace(100)
    median_par, _ = par_math.ramp_par(samples, 30)
    assert median_par >= best_par


def test_steady_par_needs_samples_and_caps():
    assert par_math.steady_par([1, 2, 3], 30, None) is None  # <4 samples
    # cap applies against previous
    samples = [1000] * 10
    capped = par_math.steady_par(samples, 30, previous_par=100)
    assert capped == 110  # would be ~1200 but capped to +10%
