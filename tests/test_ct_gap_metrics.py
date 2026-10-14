"""Tests for the pure computations behind the camera-trap gap analysis page.

Hand-checked values for the incidence estimators (Chao et al. 2014 / iNEXT
``incidence_freq``), the EEA grid conventions shared with the SDM module, the
kernel effort surface and the composite score.

Run:
    venv/Scripts/python -m pytest tests/test_ct_gap_metrics.py -v
"""
import math
from datetime import date, timedelta

import pytest

from app.camera_traps import gap_metrics as gm


# Example community: 5 species with incidences 1,1,2,3,5 over 30 units.
INC = [1, 1, 2, 3, 5]
T = 30


# ─── EEA grid ─────────────────────────────────────────────────────────────────

def test_eea_cell_code_matches_canonical_example():
    """Same convention as the SDM module: east/north always in km."""
    assert gm.eea_cell_code(5300000, 3070000, 5000) == '5kmE5300N3070'
    assert gm.eea_cell_code(5300000, 3070000, 1000) == '1kmE5300N3070'
    assert gm.eea_cell_code(5300000, 3070000, 10000) == '10kmE5300N3070'


def test_cell_origin_floors_to_grid():
    assert gm.cell_origin(5304999, 3071234, 5000) == (5300000, 3070000)
    assert gm.cell_origin(5305000, 3074999, 5000) == (5305000, 3070000)


def test_laea_roundtrip():
    x, y = gm.to_laea(23.95, 49.85)          # Lviv
    lon, lat = gm.to_wgs84(x, y)
    assert lon == pytest.approx(23.95, abs=1e-7)
    assert lat == pytest.approx(49.85, abs=1e-7)
    # LAEA false easting/northing put Ukraine well into the positive millions.
    assert 5_000_000 < x < 6_500_000 and 2_500_000 < y < 3_500_000


def test_cell_polygon_is_closed_ring():
    ring = gm.cell_polygon_wgs84(5300000, 3070000, 5000)
    assert len(ring) == 5 and ring[0] == ring[-1]


# ─── Richness ─────────────────────────────────────────────────────────────────

def test_richness_stats_hand_computed():
    r = gm.richness_stats(INC, T)
    assert (r['S_obs'], r['Q1'], r['Q2'], r['U']) == (5, 2, 1, 12)
    f0 = (29 / 30) * 4 / 2
    assert r['chao2'] == pytest.approx(5 + f0)
    A = 58 / 60
    assert r['coverage'] == pytest.approx(1 - (2 / 12) * A)
    assert r['completeness'] == pytest.approx(5 / (5 + f0))


def test_richness_without_doubletons_uses_bias_corrected_form():
    r = gm.richness_stats([1, 1, 1, 4], 40)
    assert r['chao2'] == pytest.approx(4 + (39 / 40) * 3 * 2 / 2)


def test_richness_all_common_species_is_complete():
    r = gm.richness_stats([5, 7, 9], 40)
    assert r['chao2'] == 3 and r['coverage'] == 1 and r['completeness'] == 1


def test_richness_not_estimable_below_min_units():
    r = gm.richness_stats([1, 2], gm.MIN_UNITS_FOR_ESTIMATE - 1)
    assert r['estimable'] is False
    assert r['chao2'] is None and r['coverage'] is None
    assert r['S_obs'] == 2


def test_richness_empty_but_long_effort():
    r = gm.richness_stats([], 100)
    assert r['S_obs'] == 0 and r['coverage'] == 1.0


# ─── Accumulation ─────────────────────────────────────────────────────────────

def test_rarefaction_one_unit_equals_mean_species_per_unit():
    assert gm.expected_richness(INC, T, 1) == pytest.approx(12 / 30)


def test_rarefaction_ends_at_observed():
    assert gm.expected_richness(INC, T, T) == pytest.approx(5)


def test_extrapolation_is_continuous_and_bounded_by_chao2():
    chao2 = gm.richness_stats(INC, T)['chao2']
    just_after = gm.expected_richness(INC, T, T + 1)
    assert 5 < just_after < 5.2
    far = gm.expected_richness(INC, T, 100 * T)
    assert far == pytest.approx(chao2, rel=1e-3)
    assert far <= chao2 + 1e-9


def test_accumulation_curve_monotone_and_contains_T():
    curve = gm.accumulation_curve(INC, T)
    units = [u for u, _ in curve]
    values = [v for _, v in curve]
    assert T in units
    assert units == sorted(units) and values == sorted(values)
    assert units[-1] == 2 * T


def test_units_to_coverage_reaches_target():
    m = gm.units_to_coverage(INC, T, 0.95)
    assert m > 0
    A = 58 / 60
    assert 1 - (2 / 12) * A ** (m + 1) >= 0.95
    assert 1 - (2 / 12) * A ** m < 0.95


def test_units_to_coverage_zero_when_no_singletons():
    assert gm.units_to_coverage([4, 5, 6], 40) == 0


def test_units_to_completeness_reaches_target():
    m = gm.units_to_completeness(INC, T, 0.9)
    chao2 = gm.richness_stats(INC, T)['chao2']
    assert gm.expected_richness(INC, T, T + m) >= 0.9 * chao2 - 1e-9
    assert gm.expected_richness(INC, T, T + m - 1) < 0.9 * chao2


# ─── Detection ────────────────────────────────────────────────────────────────

def test_detection_probability_and_confidence():
    p = gm.per_unit_detection_probability(10, 200)
    assert p == pytest.approx(0.05)
    assert gm.detection_confidence(p, 0) == 0
    assert gm.detection_confidence(p, 59) == pytest.approx(1 - 0.95 ** 59)
    assert gm.units_for_confidence(p, 0.95) == 59


def test_detection_probability_undefined_without_known_presence():
    assert gm.per_unit_detection_probability(0, 0) is None
    assert gm.detection_confidence(None, 100) == 0
    assert gm.units_for_confidence(None) is None


# ─── Time ─────────────────────────────────────────────────────────────────────

def test_temporal_coverage_counts_months_with_enough_days():
    start = date(2024, 6, 1)
    days = [start + timedelta(days=i) for i in range(45)]   # June full, July 15
    days += [date(2024, 12, 1)] * 3                          # December too thin
    t = gm.temporal_coverage(days)
    assert t['months'][5] == 30 and t['months'][6] == 15 and t['months'][11] == 3
    assert t['months_covered'] == 2
    assert t['seasons_covered'] == 1
    assert t['years'] == [2024]
    assert t['score'] == pytest.approx(2 / 12)


# ─── Scores ───────────────────────────────────────────────────────────────────

def test_effort_score_log_scale_and_saturation():
    assert gm.effort_score(0, 1000) == 0
    assert gm.effort_score(365, 1000) == pytest.approx(1)
    assert gm.effort_score(10_000, 1000) == 1
    assert 0.3 < gm.effort_score(10, 1000) < 0.5


def test_composite_is_zero_if_any_dimension_is_zero():
    assert gm.composite_score(1, 1, 0) == 0
    assert gm.composite_score(0, 1, 1) == 0


def test_composite_is_unknown_without_a_species_pool():
    """No pool to judge against means "cannot say", not "bad"."""
    assert gm.composite_score(1, None, 1) is None
    assert gm.composite_score(0.5, 0.5, 0.5) == pytest.approx(0.5)


# ─── Kernel surface ───────────────────────────────────────────────────────────

def test_kernel_surface_integrates_to_total_effort():
    pts = [(5_300_000, 3_070_000, 100), (5_310_000, 3_072_000, 50)]
    pixel, surface = gm.kernel_effort_surface(pts, 2000, pixel_m=250, cutoff_sigmas=4)
    total = sum(surface.values()) * (pixel / 1000) ** 2
    assert total == pytest.approx(150, rel=0.01)


def test_kernel_surface_peaks_at_camera():
    pixel, surface = gm.kernel_effort_surface([(5_300_100, 3_070_100, 10)], 1000)
    peak = max(surface, key=surface.get)
    assert peak[0] <= 5_300_100 < peak[0] + pixel
    assert peak[1] <= 3_070_100 < peak[1] + pixel


def test_kernel_surface_ignores_zero_weight():
    _, surface = gm.kernel_effort_surface([(0, 0, 0)], 1000)
    assert surface == {}


# ─── Pool sensitivity ─────────────────────────────────────────────────────────

def test_pool_sensitivity_weights_rare_species_equally():
    pool = {1: 0.5, 2: 0.01}
    # 10 units: the common species is near certain, the rare one is not
    s = gm.pool_sensitivity(pool, 10)
    assert s == pytest.approx(((1 - 0.5 ** 10) + (1 - 0.99 ** 10)) / 2)
    assert s < 0.6


def test_pool_sensitivity_counts_recorded_species_as_one():
    assert gm.pool_sensitivity({1: 0.01, 2: 0.01}, 0, detected={1, 2}) == 1
    assert gm.pool_sensitivity({1: 0.01, 2: 0.01}, 0, detected={1}) == 0.5


def test_pool_sensitivity_empty_pool():
    assert gm.pool_sensitivity({}, 100) is None



# ─── Audit follow-ups (Oct 2026) ──────────────────────────────────────────────

def test_single_singleton_without_doubletons_is_flagged_unstable():
    """Q1 = 1, Q2 = 0: bias-corrected f0 = 0, coverage = 1 on one record (iNEXT does
    the same). The value is kept but must be flagged."""
    r = gm.richness_stats([1, 30, 40], 50)
    assert r['coverage'] == 1 and r['chao2'] == 3
    assert r['unstable'] is True


def test_doubletons_make_estimate_stable():
    assert gm.richness_stats(INC, T)['unstable'] is False
    assert gm.richness_stats([5, 7], 40)['unstable'] is False      # no singletons at all


def test_unstable_needs_an_estimate_first():
    assert gm.richness_stats([1], 5)['unstable'] is False


def test_home_range_of_kernel_bandwidth():
    # 95% contour of a bivariate normal: 2*pi*sigma^2*ln(20)
    assert gm.home_range_km2(1000) == pytest.approx(18.82, abs=0.01)
    assert gm.home_range_km2(2500) == pytest.approx(117.6, abs=0.1)
