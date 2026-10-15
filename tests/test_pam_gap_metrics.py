"""Tests for the PAM gap analysis maths (app/pam/gap_metrics.py).

The estimators started as a copy of the camera-trap module's gap_metrics.py.
The submodules stay independent and the copies may evolve separately, so this
copy is tested on its own: hand-computed estimator values plus the acoustic
additions.

Run:
    venv/Scripts/python -m pytest tests/test_pam_gap_metrics.py -v
"""
import pytest

from app.pam import gap_metrics as gm


# ─── Estimators (hand-computed values) ────────────────────────────────────────

# 30 units, five species: two singletons, one doubleton.
INC = [1, 1, 2, 3, 5]
T = 30


def test_richness_stats_hand_computed():
    r = gm.richness_stats(INC, T)
    assert (r['S_obs'], r['Q1'], r['Q2'], r['U']) == (5, 2, 1, 12)
    f0 = (29 / 30) * 4 / 2
    assert r['chao2'] == pytest.approx(5 + f0)
    assert r['coverage'] == pytest.approx(1 - (2 / 12) * (58 / 60))
    assert r['completeness'] == pytest.approx(5 / (5 + f0))


def test_richness_without_doubletons_is_bias_corrected_and_unstable():
    r = gm.richness_stats([1, 1, 1, 4], 40)
    assert r['chao2'] == pytest.approx(4 + (39 / 40) * 3 * 2 / 2)
    assert r['unstable'] is True
    assert gm.richness_stats([1, 5], gm.MIN_UNITS_FOR_ESTIMATE - 1)['estimable'] is False


def test_accumulation_ends_at_observed_and_approaches_chao2():
    assert gm.expected_richness(INC, T, 1) == pytest.approx(12 / 30)
    assert gm.expected_richness(INC, T, T) == pytest.approx(5)
    chao2 = gm.richness_stats(INC, T)['chao2']
    assert gm.expected_richness(INC, T, 100 * T) == pytest.approx(chao2, rel=1e-3)
    units = [u for u, _ in gm.accumulation_curve(INC, T)]
    assert T in units and units[-1] == 2 * T


def test_units_to_coverage_reaches_target():
    m = gm.units_to_coverage(INC, T, 0.95)
    A = 58 / 60
    assert 1 - (2 / 12) * A ** (m + 1) >= 0.95 > 1 - (2 / 12) * A ** m


def test_detection_helpers():
    assert gm.per_unit_detection_probability(10, 200) == pytest.approx(0.05)
    assert gm.per_unit_detection_probability(1, 0) is None
    assert gm.detection_confidence(0.05, 20) == pytest.approx(1 - 0.95 ** 20)
    assert gm.units_for_confidence(0.05) == 59
    assert gm.pool_sensitivity({1: 0.5, 2: 0.0}, 1, {2}) == pytest.approx(0.75)
    assert gm.pool_sensitivity({}, 10) is None
    assert gm.composite_score(0.5, None, 0.5) is None
    assert gm.eea_cell_code(5300000, 3070000, 5000) == '5kmE5300N3070'


def test_kernel_conserves_hours():
    pts = [(5_300_000, 3_070_000, 120.0), (5_302_000, 3_071_000, 40.0)]
    pixel, surface = gm.kernel_effort_surface(pts, 1000)
    assert sum(surface.values()) * (pixel / 1000) ** 2 == pytest.approx(160, rel=0.02)   # 3-sigma cut loses ~1.1%


# ─── Acoustic additions ───────────────────────────────────────────────────────

def test_effort_score_is_in_recording_hours():
    assert gm.effort_score(0, 1000) == 0
    assert gm.effort_score(gm.EFFORT_TARGET_HOURS[1000], 1000) == pytest.approx(1)
    assert gm.effort_score(10 * gm.EFFORT_TARGET_HOURS[5000], 5000) == 1
    assert 0 < gm.effort_score(30, 10000) < gm.effort_score(30, 1000) < 1


def test_diel_coverage_counts_hours_with_enough_minutes():
    minutes = {h: 120 for h in range(5, 10)}       # dawn chorus only
    minutes[23] = gm.MIN_MINUTES_PER_CLOCK_HOUR     # exactly enough
    minutes[2] = gm.MIN_MINUTES_PER_CLOCK_HOUR - 5  # a stray file or two: not enough
    d = gm.diel_coverage(minutes)
    assert d['hours_covered'] == 6
    assert d['night_covered'] == 1                 # 23 h; 2 h falls short
    assert d['score'] == pytest.approx(6 / 24)
    assert len(d['minutes']) == 24 and d['minutes'][2] == 55


def test_diel_coverage_accepts_a_sequence():
    d = gm.diel_coverage([60] * 24)
    assert d['hours_covered'] == 24 and d['night_covered'] == 8 and d['score'] == 1
    assert gm.diel_coverage([])['hours_covered'] == 0
    assert gm.diel_coverage([None, 61])['hours_covered'] == 1


def test_temporal_coverage_threshold_in_recorder_days():
    from datetime import date, timedelta
    may = [date(2025, 5, 1) + timedelta(days=i) for i in range(gm.MIN_RECORDER_DAYS_PER_MONTH)]
    june = [date(2025, 6, 1)] * (gm.MIN_RECORDER_DAYS_PER_MONTH - 1)
    t = gm.temporal_coverage(may + june)
    assert t['months_covered'] == 1 and t['seasons_covered'] == 1
