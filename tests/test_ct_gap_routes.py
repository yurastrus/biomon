"""Tests for the gap analysis page and its JSON API (app/camera_traps/gap_routes.py).

fetch_gap_data (PostgreSQL-only SQL) is replaced by a stub returning synthetic
GapData; what is checked here is the route layer: parameter validation, access
rules reaching the SQL filter, caching keyed on access, and the JSON shapes.

Run:
    venv/Scripts/python -m pytest tests/test_ct_gap_routes.py -v
"""
from unittest.mock import MagicMock, patch

import pytest

from app.camera_traps import gap_routes
from tests.test_ct_gap_analysis import make_data

BASE = '/uk/camera-traps'


@pytest.fixture
def fetch_stub():
    """Patch the DB layer; yields the mock so tests can inspect its calls."""
    gap_routes._gap_cache.clear()
    stub = MagicMock(side_effect=lambda *a, **kw: make_data())
    with patch.object(gap_routes.ga, 'fetch_gap_data', stub), \
         patch.object(gap_routes, 'get_ct_session', return_value=MagicMock()), \
         patch.object(gap_routes, 'close_ct_session'):
        yield stub
    gap_routes._gap_cache.clear()


def test_page_renders(client):
    resp = client.get(f'{BASE}/analysis/gap-analysis')
    assert resp.status_code == 200
    assert b'gap-map' in resp.data


def test_page_renders_in_english(client):
    assert client.get('/en/camera-traps/analysis/gap-analysis').status_code == 200


def test_grid_api_shape(client, fetch_stub):
    resp = client.get(f'{BASE}/api/gap-analysis/grid?scale=5000')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['summary']['scale_m'] == 5000
    statuses = {c['status'] for c in body['cells']}
    assert statuses == {'data', 'empty'}
    assert {l['id'] for l in body['locations']} == {1, 2, 3}
    assert body['summary']['species_options'][0]['name'] == 'Козуля'


def test_invalid_parameters_fall_back_to_defaults(client, fetch_stub):
    body = client.get(f'{BASE}/api/gap-analysis/grid?scale=777&unit=hour&taxon=fungi'
                      '&start_date=2025-02-01&end_date=2024-01-01').get_json()
    assert body['summary']['scale_m'] == 5000 and body['summary']['unit'] == 'day'
    kwargs = fetch_stub.call_args.kwargs
    assert kwargs['taxon'] == 'mammals'
    assert kwargs['start_date'] is None and kwargs['end_date'] is None


def test_anonymous_sees_public_locations_only_and_no_qc(client, fetch_stub):
    client.get(f'{BASE}/api/gap-analysis/grid?qc_exclude=qc_poor_placement')
    args, kwargs = fetch_stub.call_args
    assert 'visibility_level = 0' in args[1]
    assert kwargs['qc_exclude'] == []


def test_admin_gets_everything_and_qc_applies(auth_client, fetch_stub):
    c = auth_client(role='admin')
    c.get(f'{BASE}/api/gap-analysis/grid?qc_exclude=qc_poor_placement')
    args, kwargs = fetch_stub.call_args
    assert args[1].strip().startswith('1=1')
    assert kwargs['qc_exclude'] == ['qc_poor_placement']


def test_cache_reuses_data_across_layers(client, fetch_stub):
    client.get(f'{BASE}/api/gap-analysis/grid?scale=5000')
    client.get(f'{BASE}/api/gap-analysis/grid?scale=10000')
    client.get(f'{BASE}/api/gap-analysis/biotopes')
    client.get(f'{BASE}/api/gap-analysis/kernel')
    assert fetch_stub.call_count == 1
    client.get(f'{BASE}/api/gap-analysis/grid?taxon=birds')
    assert fetch_stub.call_count == 2


def test_cache_is_keyed_on_access_rights(client, fetch_stub):
    """Two users with different institution access never share cached data.

    (Switching logged-in users inside one test does not work here: Flask-Login
    caches the user on ``g`` of the long-lived test app context, so the access
    change is simulated at the source instead.)
    """
    client.get(f'{BASE}/api/gap-analysis/grid')
    with patch.object(gap_routes.ct_access, 'allowed_institution_ids', return_value=[5]):
        client.get(f'{BASE}/api/gap-analysis/grid')
    assert fetch_stub.call_count == 2


def test_focus_species(client, fetch_stub):
    body = client.get(f'{BASE}/api/gap-analysis/grid?scale=5000&species_id=11').get_json()
    assert body['summary']['focus']['name'] == 'Рись'
    data_cells = [c for c in body['cells'] if c['status'] == 'data']
    assert all('focus_confidence' in c for c in data_cells)


def test_cell_detail(client, fetch_stub):
    body = client.get(f'{BASE}/api/gap-analysis/cell/5kmE5300N3070?scale=5000').get_json()
    assert body['S_obs'] == 3 and body['curve']
    assert client.get(f'{BASE}/api/gap-analysis/cell/1kmE0N0?scale=5000').status_code == 404


def test_kernel_bandwidth_whitelist(client, fetch_stub):
    assert client.get(f'{BASE}/api/gap-analysis/kernel?bandwidth=5000').get_json()['bandwidth_m'] == 5000
    assert client.get(f'{BASE}/api/gap-analysis/kernel?bandwidth=3').get_json()['bandwidth_m'] == 2500


def test_biotopes(client, fetch_stub):
    rows = client.get(f'{BASE}/api/gap-analysis/biotopes').get_json()['rows']
    assert {r['name'] for r in rows} == {'Ліс', 'Лука'}


def test_server_error_is_json_500(client):
    gap_routes._gap_cache.clear()
    with patch.object(gap_routes.ga, 'fetch_gap_data', side_effect=RuntimeError('boom')), \
         patch.object(gap_routes, 'get_ct_session', return_value=MagicMock()), \
         patch.object(gap_routes, 'close_ct_session') as close:
        resp = client.get(f'{BASE}/api/gap-analysis/grid')
    assert resp.status_code == 500 and resp.get_json() == {'error': 'Server error'}
    close.assert_called_once()


def test_hub_links_to_gap_analysis(client):
    resp = client.get(f'{BASE}/')
    assert b'/analysis/gap-analysis' in resp.data


def test_english_page_is_translated(client):
    html = client.get('/en/camera-traps/analysis/gap-analysis').get_data(as_text=True)
    assert 'Data gap analysis' in html
    assert 'Аналіз прогалин' not in html
