"""Tests for the PAM gap analysis maths (app/pam/gap_metrics.py).

The estimators are a deliberate copy of the camera-trap module's gap_metrics.py
(the two submodules must not import each other). The full estimator suite runs
against that copy in tests/test_ct_gap_metrics.py; here a parity test keeps the
two copies from drifting, and the acoustic additions are tested directly.

Run:
    venv/Scripts/python -m pytest tests/test_pam_gap_metrics.py -v
"""
import random

import pytest

from app.camera_traps import gap_metrics as ct
from app.pam import gap_metrics as gm


# ─── Parity with the camera-trap copy ─────────────────────────────────────────

def _random_samples(n=60, seed=7):
    rnd = random.Random(seed)
    for _ in range(n):
        T = rnd.randint(1, 80)
        ys = [rnd.randint(1, T) if rnd.random() < 0.5 else rnd.randint(1, 3)
              for _ in range(rnd.randint(0, 40))]
        yield ys, T


@pytest.mark.parametrize('name', ['richness_stats', 'units_to_coverage', 'units_to_completeness',
                                  'accumulation_curve'])
def test_estimators_match_camera_trap_copy(name):
    for ys, T in _random_samples():
        assert getattr(gm, name)(ys, T) == getattr(ct, name)(ys, T), (name, ys, T)


def test_detection_helpers_match_camera_trap_copy():
    for p in (None, 0, 0.01, 0.2, 0.7, 1):
        for units in (0, 1, 2.5, 30):
            assert gm.detection_confidence(p, units) == ct.detection_confidence(p, units)
        assert gm.units_for_confidence(p) == ct.units_for_confidence(p)
    pool = {1: 0.1, 2: 0.02, 3: 0.5}
    assert gm.pool_sensitivity(pool, 12.5, {3}) == ct.pool_sensitivity(pool, 12.5, {3})
    assert gm.composite_score(0.5, 0.4, 0.9) == ct.composite_score(0.5, 0.4, 0.9)
    assert gm.eea_cell_code(5300000, 3070000, 5000) == ct.eea_cell_code(5300000, 3070000, 5000)


def test_kernel_matches_camera_trap_copy():
    pts = [(5_300_000, 3_070_000, 120.0), (5_302_000, 3_071_000, 40.0)]
    assert gm.kernel_effort_surface(pts, 1000) == ct.kernel_effort_surface(pts, 1000)


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
