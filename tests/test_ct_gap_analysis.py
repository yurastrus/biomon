"""Tests for the gap analysis data layer (app/camera_traps/gap_analysis.py).

The SQL uses BOOL_OR and ANY(:list) (PostgreSQL only), so fetch_gap_data is
exercised through a mocked session that replays rows in query order. Everything
downstream of it is pure and fed synthetic GapData directly.

Run:
    venv/Scripts/python -m pytest tests/test_ct_gap_analysis.py -v
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.camera_traps import gap_analysis as ga
from app.camera_traps import gap_metrics as gm


D0 = date(2024, 5, 1)


def days(start, n):
    return {start + timedelta(days=i) for i in range(n)}


# ─── Effort days ──────────────────────────────────────────────────────────────

def test_effort_fills_short_gaps_between_verified_days():
    eff = ga.build_effort_days({1: {D0, D0 + timedelta(days=10)}}, {}, {}, max_gap_days=15)
    assert eff[1] == days(D0, 11)


def test_effort_does_not_bridge_long_gaps():
    eff = ga.build_effort_days({1: {D0, D0 + timedelta(days=40)}}, {}, {}, max_gap_days=15)
    assert eff[1] == {D0, D0 + timedelta(days=40)}


def test_deployment_counts_only_with_verified_data_inside():
    deps = {1: [(D0, D0 + timedelta(days=29))], 2: [(D0, D0 + timedelta(days=29))]}
    eff = ga.build_effort_days({1: {D0 + timedelta(days=5)}}, {}, deps)
    assert eff[1] == days(D0, 30)
    assert 2 not in eff            # deployment without any verified series


def test_unverified_only_days_are_not_effort():
    unverified = {1: {D0 + timedelta(days=3)}}
    eff = ga.build_effort_days({1: {D0, D0 + timedelta(days=6)}}, unverified, {})
    assert D0 + timedelta(days=3) not in eff[1]
    assert D0 + timedelta(days=2) in eff[1]


def test_effort_is_clipped_to_period():
    deps = {1: [(D0, D0 + timedelta(days=29))]}
    eff = ga.build_effort_days({1: {D0}}, {}, deps,
                               start_date=D0 + timedelta(days=10),
                               end_date=D0 + timedelta(days=19))
    assert eff[1] == days(D0 + timedelta(days=10), 10)


# ─── Synthetic scope ──────────────────────────────────────────────────────────

def make_data():
    """Two locations in one 5 km cell, one far away, one with no effort."""
    data = ga.GapData()
    base_x, base_y = 5_300_000, 3_070_000
    coords = {1: (base_x + 500, base_y + 500), 2: (base_x + 3500, base_y + 3500),
              3: (base_x + 60_000, base_y + 500), 4: (base_x + 1000, base_y + 4000)}
    for lid, (x, y) in coords.items():
        lon, lat = gm.to_wgs84(x, y)
        data.locations[lid] = {'name': f'L{lid}', 'lat': lat, 'lon': lon, 'x': x, 'y': y}
    data.effort_days = {1: days(D0, 30), 2: days(D0, 30), 3: days(D0, 5)}
    data.detections = {
        1: {10: {D0, D0 + timedelta(days=1)}, 11: {D0}},
        2: {10: {D0 + timedelta(days=2)}, 12: {D0, D0 + timedelta(days=9)}},
        3: {10: {D0}},
    }
    data.species = {10: {'name_uk': 'Козуля', 'name_en': 'Roe deer', 'scientific': 'Capreolus capreolus'},
                    11: {'name_uk': 'Рись', 'name_en': 'Lynx', 'scientific': 'Lynx lynx'},
                    12: {'name_uk': 'Лисиця', 'name_en': 'Red fox', 'scientific': 'Vulpes vulpes'}}
    data.biotopes = {1: [100], 2: [100, 200], 3: [200]}
    data.biotope_names = {100: ('Ліс', 'Forest'), 200: ('Лука', 'Meadow')}
    return data


def test_grid_pools_locations_of_a_cell():
    res = ga.build_grid(make_data(), 5000, include_empty=False)
    data_cells = {c['code']: c for c in res['cells'] if c['status'] == 'data'}
    assert len(data_cells) == 2
    big = data_cells['5kmE5300N3070']
    assert big['n_locations'] == 2 and big['camera_days'] == 60
    assert big['T'] == 60 and big['S_obs'] == 3
    # incidences: roe deer 3 units, lynx 1, fox 2 → Q1 = 1, Q2 = 1
    assert big['Q1'] == 1 and big['Q2'] == 1
    assert big['estimable'] and big['coverage'] is not None
    assert big['species'][0] == 'Козуля'
    small = data_cells['5kmE5360N3070']
    assert small['estimable'] is False and small['coverage'] is None
    assert small['composite'] == 0


def test_grid_location_without_effort_is_ignored():
    res = ga.build_grid(make_data(), 1000, include_empty=False)
    assert sum(c['n_locations'] for c in res['cells']) == 3


def test_grid_week_units_shrink_T():
    res = ga.build_grid(make_data(), 5000, unit=ga.UNIT_WEEK, include_empty=False)
    big = next(c for c in res['cells'] if c['code'] == '5kmE5300N3070')
    assert big['T'] < 20 and big['camera_days'] == 60


def test_grid_focus_species_confidence():
    res = ga.build_grid(make_data(), 5000, focus_species_id=11, include_empty=False)
    by_code = {c['code']: c for c in res['cells']}
    assert by_code['5kmE5300N3070']['focus_detected'] is True
    assert by_code['5kmE5300N3070']['focus_confidence'] == 1.0
    far = by_code['5kmE5360N3070']
    assert far['focus_detected'] is False
    # lynx: 1 unit with a record out of 30 units at location 1 → p = 1/30
    assert far['focus_confidence'] == pytest.approx(1 - (29 / 30) ** 5, abs=1e-3)
    focus = res['summary']['focus']
    assert focus['p_unit'] == pytest.approx(1 / 30, abs=1e-4)
    assert focus['camera_days_for_95'] == gm.units_for_confidence(1 / 30)


def test_grid_adds_empty_cells_inside_study_area():
    res = ga.build_grid(make_data(), 5000)
    empties = [c for c in res['cells'] if c['status'] == 'empty']
    assert empties, 'the closed study area must contain cells without cameras'
    codes = {c['code'] for c in res['cells']}
    assert len(codes) == len(res['cells'])     # no duplicates
    # the 60 km gap between the clusters is wider than the closing distance,
    # so the area must not swallow the whole strip between them
    assert res['summary']['n_empty_cells'] == len(empties)


def test_species_options_sorted_by_detection_days():
    opts = ga.species_options(make_data(), 'en')
    assert [o['name'] for o in opts] == ['Roe deer', 'Red fox', 'Lynx']


def test_cell_detail_curve_ends_at_observed():
    data = make_data()
    res = ga.build_grid(data, 5000, include_empty=False)
    det = ga.cell_detail(res, '5kmE5300N3070', data)
    assert det['S_obs'] == 3
    at_T = next(p for p in det['curve'] if p['units'] == det['T'])
    assert at_T['S'] == pytest.approx(3)
    assert [l['name'] for l in det['locations']] == ['L1', 'L2']
    assert ga.cell_detail(res, 'nope', data) is None


def test_biotope_summary_counts_location_in_each_biotope():
    rows = {r['name']: r for r in ga.biotope_summary(make_data())}
    assert rows['Ліс']['n_locations'] == 2 and rows['Ліс']['camera_days'] == 60
    assert rows['Лука']['n_locations'] == 2 and rows['Лука']['camera_days'] == 35
    assert rows['Лука']['S_obs'] == 2


def test_kernel_layer_conserves_effort():
    k = ga.build_kernel(make_data(), 2000)
    assert k['pixels'] and k['max'] > 0
    total = sum(p['value'] for p in k['pixels']) * (k['pixel_m'] / 1000) ** 2
    assert total == pytest.approx(65, rel=0.02)


def test_location_points_skip_locations_without_effort():
    pts = ga.location_points(make_data())
    assert sorted(p['id'] for p in pts) == [1, 2, 3]


# ─── fetch_gap_data with a replayed session ───────────────────────────────────

def _session(*result_sets):
    session = MagicMock()
    session.execute.side_effect = [MagicMock(fetchall=MagicMock(return_value=r))
                                   for r in result_sets]
    return session


def test_fetch_gap_data_assembles_effort_and_detections():
    session = _session(
        [(1, 'L1', 49.8, 23.9), (2, 'L2', None, None)],          # locations
        [(1, D0, True), (1, D0 + timedelta(days=5), True),
         (1, D0 + timedelta(days=2), False), (1, date(1900, 1, 1), True)],  # series days
        [(1, D0, D0 + timedelta(days=9))],                         # deployments
        [(1, D0 + timedelta(days=5), 10, 'Козуля', 'Roe deer', 'Capreolus capreolus'),
         (1, D0, 8, 'Пес', 'Dog', 'Canis familiaris')],           # detections
        [(1, 100, 'Ліс', 'Forest')],                               # biotopes
    )
    data = ga.fetch_gap_data(session, '1=1', {}, exclude_species_ids={8})
    assert list(data.locations) == [1]                 # no coordinates → skipped
    assert data.effort_days[1] == days(D0, 10) - {D0 + timedelta(days=2)}
    assert data.detections == {1: {10: {D0 + timedelta(days=5)}}}
    assert data.biotopes == {1: [100]}
    assert session.execute.call_count == 5


def test_fetch_gap_data_stops_when_scope_is_empty():
    session = _session([])
    data = ga.fetch_gap_data(session, '1=1', {})
    assert data.locations == {} and session.execute.call_count == 1


def test_deployment_flag_sql_validates_flags():
    sql = ga._deployment_flag_sql(['qc_poor_placement', 'drop table', 'qc_local_datetime_issue'])
    assert 'qc_poor_placement' in sql and 'drop table' not in sql
    assert "COALESCE(d.qc_local_datetime_issue, '') = ''" in sql
    assert 'qc_data_not_usable' in sql


def test_pool_excludes_species_with_too_few_units():
    data = make_data()
    pool = ga.species_detection_probabilities(data, ga.UNIT_DAY, ga.MIN_POOL_UNITS)
    # roe deer: 4 units; fox: 2; lynx: 1 → only roe deer reaches the pool
    assert set(pool) == {10}


def test_grid_reports_pool_sensitivity_and_likely_missed(monkeypatch):
    monkeypatch.setattr(ga, 'POOL_RADIUS_M', 100_000)   # the far cell is 60 km away
    data = make_data()
    data.detections[1][12] = {D0 + timedelta(days=i) for i in range(3, 8)}  # fox now common
    res = ga.build_grid(data, 5000, include_empty=False)
    by_code = {c['code']: c for c in res['cells']}
    big = by_code['5kmE5300N3070']
    assert big['pool_sensitivity'] == 1.0            # both pool species recorded here
    far = by_code['5kmE5360N3070']
    assert 0 < far['pool_sensitivity'] < 1
    assert [m['name'] for m in far['likely_missed']] == ['Лисиця']
    assert [p['name'] for p in res['summary']['pool']][0] in ('Лисиця', 'Козуля')


def test_local_pool_drops_species_recorded_far_away():
    data = make_data()
    pool = {10: 0.1, 11: 0.05}
    # lynx (11) only at location 1; location 3 is 60 km east
    loc3 = data.locations[3]
    assert set(ga.local_pool(data, pool, loc3['x'], loc3['y'], radius_m=10_000)) == {10}
    assert set(ga.local_pool(data, pool, loc3['x'], loc3['y'], radius_m=100_000)) == {10, 11}


def test_far_cell_is_not_judged_against_a_distant_pool():
    data = make_data()
    data.detections[1][12] = {D0 + timedelta(days=i) for i in range(3, 8)}
    res = ga.build_grid(data, 5000, include_empty=False)
    far = next(c for c in res['cells'] if c['code'] == '5kmE5360N3070')
    # within 50 km only the far location's own roe deer: recorded, so pool = {roe deer}
    assert far['pool_size'] == 1 and far['pool_sensitivity'] == 1.0
