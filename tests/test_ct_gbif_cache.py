"""Tests for the GBIF cache of the gap analysis page (app/camera_traps/gbif_cache.py)
and its use on the grid and in the routes.

GBIF and ct_db are never touched: requests go through a fake session that
serves canned JSON, and the database through mocked connections.

Run:
    venv/Scripts/python -m pytest tests/test_ct_gbif_cache.py -v
"""
from collections import Counter
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.camera_traps import gap_analysis as ga
from app.camera_traps import gap_metrics as gm
from app.camera_traps import gbif_cache as gc
from app.camera_traps import gap_routes
from tests.test_ct_gap_analysis import make_data

# A point inside cell 1kmE5300N3070 of make_data()'s first location.
X, Y = 5_300_500, 3_070_500
LON, LAT = gm.to_wgs84(X, Y)


def rec(**kw):
    base = {'speciesKey': 2440940, 'taxonKey': 2440940, 'familyKey': 5310 + 1,
            'datasetKey': 'other', 'decimalLatitude': LAT, 'decimalLongitude': LON,
            'year': 2020, 'coordinateUncertaintyInMeters': 30, 'occurrenceStatus': 'PRESENT'}
    base.update(kw)
    return base


# ─── Pure helpers ─────────────────────────────────────────────────────────────

def test_year_window_is_last_n_years():
    assert gc.gbif_year_window(date(2026, 10, 16)) == (2007, 2026)
    assert gc.gbif_year_window(date(2026, 1, 1), years_back=5) == (2022, 2026)
    assert gc.GBIF_YEARS_BACK == 20 and gc.FETCH_YEARS_BACK >= gc.GBIF_YEARS_BACK


@pytest.mark.parametrize('value, bucket', [
    (None, None), ('', None), ('abc', None), (3, 1000), (1000, 1000),
    (1001, 5000), (5000, 5000), (9000, 10000), (10001, False),
])
def test_uncertainty_bucket(value, bucket):
    assert gc.uncertainty_bucket(value) is bucket if bucket in (None, False) else gc.uncertainty_bucket(value) == bucket


def test_usable_at_scale():
    assert gc.usable_at_scale(1000, 1000) and not gc.usable_at_scale(5000, 1000)
    assert gc.usable_at_scale(5000, 5000) and gc.usable_at_scale(10000, 10000)
    assert not gc.usable_at_scale(None, 1000)
    assert gc.usable_at_scale(None, gc.UNKNOWN_UNCERTAINTY_MIN_SCALE_M)


def test_classify_keeps_a_good_record():
    key, reason = gc.classify(rec())
    assert reason is None
    assert key == (2440940, 5300, 3070, 2020, 1000)


@pytest.mark.parametrize('overrides, reason', [
    ({'datasetKey': 'own'}, 'own_dataset'),
    ({'taxonKey': 6164210, 'speciesKey': 5219173}, 'excluded_taxon'),   # dog folded into wolf
    ({'familyKey': 5310}, 'excluded_taxon'),                             # seals
    ({'coordinateUncertaintyInMeters': 28000}, 'coarse'),                # obscured record
    ({'occurrenceStatus': 'ABSENT'}, 'absent'),
    ({'year': None}, 'incomplete'),
    ({'speciesKey': None}, 'incomplete'),
])
def test_classify_drops_with_reason(overrides, reason):
    key, why = gc.classify(rec(**overrides), excluded_datasets={'own'})
    assert key is None and why == reason


def test_classify_checks_the_study_area():
    from shapely.geometry import box
    from shapely.prepared import prep
    inside = prep(box(X - 100, Y - 100, X + 100, Y + 100))
    outside = prep(box(X + 10_000, Y, X + 11_000, Y + 1_000))
    assert gc.classify(rec(), inside)[1] is None
    assert gc.classify(rec(), outside) == (None, 'outside_area')


def test_aggregate_counts_and_reasons():
    counts, dropped = gc.aggregate([rec(), rec(), rec(year=2021), rec(datasetKey='own')],
                                   excluded_datasets={'own'})
    assert counts[(2440940, 5300, 3070, 2020, 1000)] == 2
    assert sum(counts.values()) == 3 and dropped == Counter({'own_dataset': 1})


def test_area_bboxes_merge_overlapping_parts():
    from shapely.geometry import box
    from shapely.ops import unary_union
    near = unary_union([box(X, Y, X + 5000, Y + 5000), box(X + 6000, Y, X + 9000, Y + 5000)])
    assert len(gc.area_bboxes(near, pad_deg=0.05)) == 1      # padding makes them overlap
    far = unary_union([box(X, Y, X + 5000, Y + 5000), box(X + 500_000, Y, X + 505_000, Y + 5000)])
    assert len(gc.area_bboxes(far)) == 2
    assert gc.area_bboxes(None) == []


def test_pick_vernacular_prefers_flag_then_consensus():
    names = [{'language': 'ukr', 'vernacularName': 'кролик свійський декоративний'},
             {'language': 'ukr', 'vernacularName': 'кріль дикий'},
             {'language': 'ukr', 'vernacularName': 'Кріль дикий'},
             {'language': 'eng', 'vernacularName': 'European Rabbit'}]
    assert gc.pick_vernacular(names, 'ukr') == 'Кріль дикий'
    assert gc.pick_vernacular(names, 'eng') == 'European Rabbit'
    assert gc.pick_vernacular(names + [{'language': 'ukr', 'vernacularName': 'Кролик', 'preferred': True}],
                              'ukr') == 'Кролик'
    assert gc.pick_vernacular([], 'ukr') is None


# ─── GBIF API (fake session) ──────────────────────────────────────────────────

class FakeSession:
    """Serves occurrence/search: `total` records per year range, split on demand."""

    def __init__(self, per_year=10):
        self.per_year = per_year
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        y0, y1 = (int(v) for v in params['year'].split(','))
        count = (y1 - y0 + 1) * self.per_year
        limit, offset = params.get('limit', 20), params.get('offset', 0)
        n = max(0, min(limit, count - offset))
        resp = MagicMock(status_code=200)
        resp.json.return_value = {'count': count, 'results': [rec(year=y0) for _ in range(n)]}
        return resp


def test_fetch_records_pages_through_results():
    s = FakeSession(per_year=400)
    out = list(gc.fetch_records(s, 732, (23, 50, 24, 51), 2020, 2021))
    assert len(out) == 800
    assert s.calls[0][1]['limit'] == 0                      # count first
    assert all(c[1]['limit'] in (0, gc.PAGE_SIZE) for c in s.calls)
    assert 'POLYGON((23 50,24 50,24 51,23 51,23 50))' == s.calls[0][1]['geometry']


def test_fetch_records_splits_years_past_offset_limit(monkeypatch):
    monkeypatch.setattr(gc, 'OFFSET_LIMIT', 1000)
    s = FakeSession(per_year=300)
    out = list(gc.fetch_records(s, 732, (23, 50, 24, 51), 2017, 2020))   # 1200 records > 1000
    assert len(out) == 1200
    assert {c[1]['year'] for c in s.calls if c[1]['limit'] == 0} >= {'2017,2020', '2017,2018', '2019,2020'}


def test_get_retries_then_fails(monkeypatch):
    monkeypatch.setattr(gc.time, 'sleep', lambda s: None)
    session = MagicMock()
    session.get.return_value = MagicMock(status_code=503)
    with pytest.raises(RuntimeError):
        gc._get(session, 'occurrence/search')
    assert session.get.call_count == gc.RETRIES


# ─── Reading the cache and the grid ───────────────────────────────────────────

def make_gbif():
    taxa = {1: {'scientific_name': 'Capreolus capreolus', 'name_uk': 'Козуля', 'name_en': 'Roe deer', 'ct_species_id': 10},
            2: {'scientific_name': 'Mustela erminea', 'name_uk': 'Горностай', 'name_en': 'Stoat', 'ct_species_id': None},
            3: {'scientific_name': 'Lynx lynx', 'name_uk': 'Рись', 'name_en': 'Lynx', 'ct_species_id': 11}}
    rows = [(1, 5300, 3070, 1000, 4),      # roe deer in the camera cell, precise
            (2, 5300, 3070, 1000, 2),      # stoat there too: cameras never recorded it
            (3, 5303, 3073, None, 1),      # lynx, no stated uncertainty: 5 km and up only
            (2, 5306, 3070, 1000, 1)]      # stoat in an empty cell of the study area
    return gc.GbifData(taxa, rows, datetime(2026, 10, 16), (2007, 2026))


def test_by_cell_sums_to_coarser_grids_and_applies_uncertainty():
    g = make_gbif()
    one = g.by_cell(1000)
    assert one[(5_300_000, 3_070_000)] == {1: 4, 2: 2}
    assert (5_303_000, 3_073_000) not in one                    # unknown uncertainty at 1 km
    five = g.by_cell(5000)
    assert five[(5_300_000, 3_070_000)] == {1: 4, 2: 2, 3: 1}
    assert five[(5_305_000, 3_070_000)] == {2: 1}


def test_grid_cells_get_gbif_fields_and_knowledge():
    res = ga.build_grid(make_data(), 5000, gbif=make_gbif())
    cells = {c['code']: c for c in res['cells']}
    cam = cells['5kmE5300N3070']
    assert cam['gbif_records'] == 7 and cam['gbif_species'] == 3
    assert [m['name'] for m in cam['gbif_missed']] == ['Горностай']   # roe deer and lynx are on camera here
    assert cam['knowledge'] == 'both'
    assert cells['5kmE5360N3070']['knowledge'] == 'camera'
    empty_gbif = cells['5kmE5305N3070']
    assert empty_gbif['status'] == 'empty' and empty_gbif['knowledge'] == 'gbif'
    assert any(c['knowledge'] == 'none' for c in res['cells'])
    gb = res['summary']['gbif']
    assert gb['available'] and (gb['year_from'], gb['year_to']) == (2007, 2026)
    assert [s['name'] for s in gb['not_on_cameras']] == ['Горностай']
    assert gb['not_on_cameras'][0]['records'] == 3 and gb['not_on_cameras'][0]['cells'] == 2


def test_grid_without_gbif_is_unchanged():
    res = ga.build_grid(make_data(), 5000, include_empty=False)
    assert res['summary']['gbif'] == {'available': False}
    assert 'knowledge' not in res['cells'][0]


def test_load_gbif_returns_none_when_tables_are_missing():
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError('relation "gbif_taxa" does not exist')
    assert gc.load_gbif(engine) is None


# ─── Refresh (fake session + mocked engine) ───────────────────────────────────

def test_refresh_dry_run_reads_but_writes_nothing():
    conn = MagicMock()
    loc = MagicMock()
    loc.fetchall.return_value = [(1, LAT, LON)]
    species = MagicMock()
    species.fetchall.return_value = []
    conn.execute.side_effect = [loc, species]
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn

    class Session(FakeSession):
        def get(self, url, params=None, timeout=None):
            if url.endswith('occurrence/search'):
                return super().get(url, params, timeout)
            resp = MagicMock(status_code=200)
            resp.json.return_value = ({'results': [{'language': 'ukr', 'vernacularName': 'Тест'}]}
                                      if url.endswith('vernacularNames')
                                      else {'canonicalName': 'Testus testus', 'order': 'Carnivora', 'family': 'Felidae'})
            return resp

    with patch.object(gc, '_excluded_datasets', return_value=set()):
        summary = gc.refresh_gbif_cache(engine, session=Session(per_year=1), today=date(2026, 1, 1),
                                        write=False)
    engine.begin.assert_not_called()
    assert summary['records_kept'] > 0 and summary['species'] == 1
    assert summary['taxa'][2440940]['name_uk'] == 'Тест'
    assert summary['years'] == [2026 - gc.FETCH_YEARS_BACK + 1, 2026]


# ─── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def stubs():
    gap_routes._gap_cache.clear()
    fetch = MagicMock(side_effect=lambda *a, **kw: make_data())
    with patch.object(gap_routes.ga, 'fetch_gap_data', fetch), \
         patch.object(gap_routes, 'get_ct_session', return_value=MagicMock()), \
         patch.object(gap_routes, 'close_ct_session'), \
         patch.object(gap_routes, 'get_ct_engine', return_value=MagicMock()), \
         patch.object(gap_routes.gc, 'load_gbif', return_value=make_gbif()) as load:
        yield load
    gap_routes._gap_cache.clear()


def test_grid_api_includes_gbif_for_mammals_only(auth_client, stubs):
    client = auth_client(role='viewer')
    body = client.get('/uk/camera-traps/api/gap-analysis/grid?scale=5000').get_json()
    assert body['summary']['gbif']['available']
    birds = client.get('/uk/camera-traps/api/gap-analysis/grid?scale=5000&taxon=birds').get_json()
    assert birds['summary']['gbif'] == {'available': False}


def test_refresh_is_admin_only(auth_client):
    client = auth_client(role='manager')
    with patch.object(gap_routes.gc, 'start_async_refresh') as start:
        resp = client.post('/uk/camera-traps/api/gap-analysis/gbif/refresh')
    assert resp.status_code == 403
    start.assert_not_called()


def test_admin_starts_refresh(auth_client):
    client = auth_client(role='admin')
    with patch.object(gap_routes, 'get_ct_engine', return_value=MagicMock()), \
         patch.object(gap_routes.gc, 'start_async_refresh', side_effect=[True, False]):
        assert client.post('/uk/camera-traps/api/gap-analysis/gbif/refresh').status_code == 202
        assert client.post('/uk/camera-traps/api/gap-analysis/gbif/refresh').status_code == 409


def test_status_endpoint(auth_client):
    client = auth_client(role='viewer')
    with patch.object(gap_routes, 'get_ct_engine', return_value=MagicMock()), \
         patch.object(gap_routes.gc, 'get_status', return_value={'status': 'completed', 'message': 'ok'}):
        assert client.get('/uk/camera-traps/api/gap-analysis/gbif/status').get_json()['status'] == 'completed'


def test_page_has_gbif_layers_and_admin_button(auth_client):
    html = auth_client(role='admin').get('/uk/camera-traps/analysis/gap-analysis').get_data(as_text=True)
    assert 'value="gbif"' in html and 'value="knowledge"' in html
    assert 'id="gbif-refresh"' in html


def test_page_hides_refresh_from_non_admin(auth_client):
    html = auth_client(role='viewer').get('/uk/camera-traps/analysis/gap-analysis').get_data(as_text=True)
    assert 'id="gbif-table"' in html and 'id="gbif-refresh"' not in html
