from app.services.scoring_service import segment_stars


def test_run1_participation_star():
    assert segment_stars(120, None) == 1


def test_under_par_two_stars():
    assert segment_stars(100, 120) == 2
    assert segment_stars(120, 120) == 2


def test_within_125_par_one_star():
    assert segment_stars(140, 120) == 1
    assert segment_stars(150, 120) == 1  # exactly 1.25x


def test_over_par_zero():
    assert segment_stars(200, 120) == 0


def test_no_elapsed_zero():
    assert segment_stars(None, 120) == 0
