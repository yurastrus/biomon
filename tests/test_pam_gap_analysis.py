"""Tests for the PAM gap analysis data layer (app/pam/gap_analysis.py).

The SQL uses ANY(:list), AT TIME ZONE and FILTER (PostgreSQL only), so
fetch_gap_data is exercised through a mocked connection that replays rows in
query order. Everything downstream of it is pure and fed synthetic GapData.

Run:
    venv/Scripts/python -m pytest tests/test_pam_gap_analysis.py -v
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.pam import gap_analysis as ga
from app.pam import gap_metrics as gm


D0 = date(2025, 5, 1)


def days(start, n):
    return {start + timedelta(days=i) for i in range(n)}


def make_raw():
    """Two recorders in one 5 km cell, one far away, one without recordings.

    Scores are what fetch_gap_data returns: best score per location, species, day.
    """
    data = ga.GapData()
    base_x, base_y = 5_300_000, 3_070_000
    coords = {1: (base_x + 500, base_y + 500), 2: (base_x + 3500, base_y + 3500),
              3: (base_x + 60_000, base_y + 500), 4: (base_x + 1000, base_y + 4000)}
    for lid, (x, y) in coords.items():
        lon, lat = gm.to_wgs84(x, y)
        data.locations[lid] = {'name': f'Т{lid}', 'name_en': f'P{lid}', 'lat': lat, 'lon': lon,
                               'x': x, 'y': y}
    data.effort_days = {1: days(D0, 30), 2: days(D0, 30), 3: days(D0, 5)}
    data.effort_minutes = {lid: {d: 360.0 for d in ds} for lid, ds in data.effort_days.items()}
    dawn = [0.0] * 24
    for h in range(4, 10):
        dawn[h] = 1800.0
    data.hour_minutes = {1: list(dawn), 2: list(dawn), 3: [90.0] * 24}
    data.scores = {
        # robin: confident, two days; tawny owl: one weak score
        1: {10: {D0: 0.99, D0 + timedelta(days=1): 0.97}, 11: {D0: 0.75}},
        2: {10: {D0 + timedelta(days=2): 0.96}, 12: {D0: 0.95, D0 + timedelta(days=9): 0.9}},
        3: {10: {D0: 0.98}},
    }
    data.confirmed = {}
    data.species = {10: {'name_uk': 'Вільшанка', 'name_en': 'Robin', 'scientific': 'Erithacus rubecula'},
                    11: {'name_uk': 'Сова сіра', 'name_en': 'Tawny owl', 'scientific': 'Strix aluco'},
                    12: {'name_uk': 'Зяблик', 'name_en': 'Chaffinch', 'scientific': 'Fringilla coelebs'}}
    data.biotopes = {1: [100], 2: [100, 200], 3: [200]}
    data.biotope_names = {100: ('Ліс', 'Forest'), 200: ('Лука', 'Meadow')}
    return data


def make_data(threshold=ga.DEFAULT_THRESHOLD):
    return ga.apply_threshold(make_raw(), threshold)


# ─── Threshold ────────────────────────────────────────────────────────────────

def test_threshold_defaults_and_range():
    assert (ga.MIN_THRESHOLD, ga.MAX_THRESHOLD, ga.DEFAULT_THRESHOLD) == (0.7, 1.0, 0.95)


@pytest.mark.parametrize('raw, expected', [
    ('0.95', 0.95), (0.5, 0.7), ('1.7', 1.0), ('abc', 0.95), (None, 0.95),
    ('nan', 0.95), ('0.8333', 0.83),
])
def test_clamp_threshold(raw, expected):
    assert ga.clamp_threshold(raw) == expected


def test_threshold_is_inclusive():
    data = make_data(0.95)
    assert data.detections[2][12] == {D0}           # 0.95 kept, 0.90 dropped
    assert 11 not in data.detections.get(1, {})     # 0.75 dropped


def test_lower_threshold_keeps_more():
    assert make_data(0.7).detections[1][11] == {D0}
    assert make_data(0.7).detections[2][12] == {D0, D0 + timedelta(days=9)}


def test_confirmed_days_count_at_any_threshold():
    raw = make_raw()
    raw.confirmed = {1: {11: {D0 + timedelta(days=3)}}, 4: {12: {D0}}}
    data = ga.apply_threshold(raw, 0.99)
    assert data.detections[1][11] == {D0 + timedelta(days=3)}
    assert data.detections[4][12] == {D0}


def test_apply_threshold_does_not_touch_the_cached_data():
    raw = make_raw()
    ga.apply_threshold(raw, 0.7)
    assert raw.detections == {} and raw.threshold is None


def test_threshold_effect_compares_with_slider_minimum():
    effect = ga.threshold_effect(make_data(0.95))
    assert effect['species'] == 2 and effect['species_at_min'] == 3
    assert effect['detection_days'] == 5 and effect['detection_days_at_min'] == 7
    assert effect['min_threshold'] == ga.MIN_THRESHOLD


# ─── Grid ─────────────────────────────────────────────────────────────────────

def test_default_unit_is_recorder_week():
    assert ga.DEFAULT_UNIT == ga.UNIT_WEEK


def test_grid_pools_locations_of_a_cell():
    res = ga.build_grid(make_data(0.7), 5000, unit=ga.UNIT_DAY, include_empty=False)
    cells = {c['code']: c for c in res['cells']}
    shared = cells['5kmE5300N3070']
    assert shared['n_locations'] == 2
    assert shared['recorder_days'] == 60 and shared['T'] == 60
    assert shared['hours'] == 360.0
    assert shared['S_obs'] == 3
    assert shared['diel_hours'] == 6 and shared['diel_night_hours'] == 1   # 04:00 only
    assert 0 < shared['diel_score'] < 1
    assert cells['5kmE5360N3070']['diel_hours'] == 24
    assert res['summary']['threshold'] == 0.7


def test_location_without_recordings_is_not_a_cell():
    res = ga.build_grid(make_data(), 1000, include_empty=False)
    codes = {c['code'] for c in res['cells']}
    assert '1kmE5301N3074' not in codes      # location 4


def test_grid_adds_empty_cells_inside_study_area():
    res = ga.build_grid(make_data(), 5000)
    empties = [c for c in res['cells'] if c['status'] == 'empty']
    assert empties and all(c['hours'] == 0 for c in empties)
    assert res['summary']['n_empty_cells'] == len(empties)


def test_effort_score_uses_recording_hours():
    res = ga.build_grid(make_data(), 1000, include_empty=False)
    cell = next(c for c in res['cells'] if c['code'] == '1kmE5300N3070')
    assert cell['effort_score'] == pytest.approx(round(gm.effort_score(180, 1000), 3))


def test_focus_species_confidence():
    res = ga.build_grid(make_data(), 5000, focus_species_id=12, include_empty=False)
    cells = {c['code']: c for c in res['cells']}
    assert cells['5kmE5300N3070']['focus_detected'] is True
    far = cells['5kmE5360N3070']
    assert far['focus_detected'] is False and 0 <= far['focus_confidence'] < 1
    assert res['summary']['focus']['name'] == 'Зяблик'
    p = ga.species_detection_probabilities(make_data(), ga.UNIT_WEEK)[12]
    assert res['summary']['focus']['recorder_days_for_95'] == gm.units_for_confidence(p) * 7


def test_detection_exposure_counts_days_not_partial_weeks():
    data = make_data()
    data.effort_days[3] = {D0 + timedelta(weeks=i) for i in range(10)}
    res = ga.build_grid(data, 5000, unit=ga.UNIT_WEEK, include_empty=False)
    far = next(c for c in res['cells'] if c['code'] == '5kmE5360N3070')
    assert far['T'] == 10
    assert far['exposure_units'] == pytest.approx(10 / 7, abs=0.01)


def test_cell_detail_has_curve_diel_and_locations():
    data = make_data()
    grid = ga.build_grid(data, 5000, include_empty=False)
    d = ga.cell_detail(grid, '5kmE5300N3070', data, lang='en')
    assert d['curve'][-1]['units'] >= d['T']
    assert len(d['diel_minutes']) == 24 and d['diel_minutes'][5] == 3600.0
    assert {l['name'] for l in d['locations']} == {'P1', 'P2'}
    assert d['species'][0]['name'] == 'Robin'
    assert ga.cell_detail(grid, 'nope', data) is None


def test_biotope_summary_counts_location_in_each_biotope():
    rows = {r['name']: r for r in ga.biotope_summary(make_data())}
    assert rows['Ліс']['n_locations'] == 2 and rows['Лука']['n_locations'] == 2
    assert rows['Ліс']['hours'] == 360.0


def test_kernel_conserves_recording_hours():
    k = ga.build_kernel(make_data(), 1000)
    pixel_km2 = (k['pixel_m'] / 1000) ** 2
    total = sum(p['value'] for p in k['pixels']) * pixel_km2
    assert total == pytest.approx(180 + 180 + 30, rel=0.02)
    assert k['home_range_km2'] == pytest.approx(gm.home_range_km2(1000), abs=0.1)


def test_location_points_use_language_and_skip_empty():
    pts = {p['id']: p for p in ga.location_points(make_data(), lang='en')}
    assert set(pts) == {1, 2, 3}
    assert pts[1]['name'] == 'P1' and pts[1]['hours'] == 180.0 and pts[1]['diel_hours'] == 6


def test_species_options_sorted_by_detection_days():
    opts = ga.species_options(make_data())
    assert opts[0]['name'] == 'Вільшанка' and opts[0]['detection_days'] == 4


# ─── fetch_gap_data ───────────────────────────────────────────────────────────

def _conn(*row_sets):
    conn = MagicMock()
    results = []
    for rows in row_sets:
        r = MagicMock()
        r.fetchall.return_value = rows
        results.append(r)
    conn.execute.side_effect = results
    return conn


def test_fetch_gap_data_assembles_effort_scores_and_verification():
    lon, lat = 23.5, 50.0
    conn = _conn(
        [(1, 'Точка', None, lat, lon), (2, 'Без координат', 'No coords', None, None)],
        [(1, D0, 300), (1, D0 + timedelta(days=1), 60), (9, D0, 5)],     # 9: out of scope
        [(1, 5, 300), (1, 6, 60), (1, None, 5)],
        [(1, D0, 10, 0.97, False), (1, D0 + timedelta(days=1), 10, None, True),
         (1, D0, 11, 0.8, False)],
        [(10, 'Вільшанка', 'Robin', 'Erithacus rubecula'), (11, None, None, 'Strix aluco')],
        [(1, 100, 'Ліс', 'Forest')],
    )
    data = ga.fetch_gap_data(conn, '1=1', {}, start_date=D0, end_date=D0 + timedelta(days=5),
                             species_condition='s.required_role IS NULL')
    assert set(data.locations) == {1}
    assert data.locations[1]['name_en'] == 'Точка'
    assert data.effort_days == {1: {D0, D0 + timedelta(days=1)}}
    assert data.effort_minutes[1][D0] == 300
    assert data.hour_minutes[1][5] == 300 and data.hour_minutes[1][6] == 60
    assert data.scores == {1: {10: {D0: 0.97}, 11: {D0: 0.8}}}
    assert data.confirmed == {1: {10: {D0 + timedelta(days=1)}}}
    assert data.species[11]['name_uk'] == 'Strix aluco'
    assert data.biotopes == {1: [100]}

    sql = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert 'AT TIME ZONE :tz' in sql[1]
    assert 'verification_result IS DISTINCT FROM 0' in sql[3]
    assert 's.required_role IS NULL' in sql[3]
    assert 'd.confidence >= :confidence' in sql[3]
    params = conn.execute.call_args_list[3].args[1]
    assert params['confidence'] == ga.MIN_THRESHOLD
    assert params['classes'] == ['Aves']
    assert params['start_date'] == D0


def test_fetch_gap_data_stops_when_scope_is_empty():
    conn = _conn([])
    data = ga.fetch_gap_data(conn, '1=1', {})
    assert data.locations == {} and conn.execute.call_count == 1


def test_fetch_gap_data_skips_species_query_without_detections():
    conn = _conn([(1, 'Т', 'P', 50.0, 23.5)], [(1, D0, 60)], [], [], [])
    data = ga.fetch_gap_data(conn, '1=1', {}, taxon='bats')
    assert conn.execute.call_count == 5
    assert data.species == {}
    assert conn.execute.call_args_list[3].args[1]['classes'] == ['Mammalia']
