"""Tests for the gap analysis page and its JSON API (app/camera_traps/gap_routes.py).

fetch_gap_data (PostgreSQL-only SQL) is replaced by a stub returning synthetic
GapData; what is checked here is the route layer: access (page and API for every
signed-in user, data limited by institution access), parameter validation, access rules reaching the SQL
filter, caching keyed on access, and the JSON shapes.

One user per test: Flask-Login caches the user on ``g`` of the long-lived test
app context, so switching users inside a test silently keeps the first one.

Run:
    venv/Scripts/python -m pytest tests/test_ct_gap_routes.py -v
"""
from unittest.mock import MagicMock, patch

import pytest

from app.camera_traps import gap_routes
from tests.test_ct_gap_analysis import make_data

BASE = '/uk/camera-traps'
PAGE = f'{BASE}/analysis/gap-analysis'


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


@pytest.fixture
def viewer(auth_client):
    """Signed-in user without institutions or admin rights."""
    return auth_client(role='viewer')


@pytest.fixture
def admin(auth_client):
    return auth_client(role='admin')


@pytest.fixture
def manager(auth_client):
    return auth_client(role='manager')


# ─── Access ───────────────────────────────────────────────────────────────────

def test_page_renders_for_admin(admin):
    resp = admin.get(PAGE)
    assert resp.status_code == 200
    assert b'gap-map' in resp.data


def test_page_renders_for_manager(manager):
    assert manager.get(PAGE).status_code == 200


def test_page_open_to_any_signed_in_user(viewer):
    assert gap_routes.GAP_ANALYSIS_MIN_ROLE is None
    assert viewer.get(PAGE).status_code == 200


def test_page_needs_login(client):
    resp = client.get(PAGE)
    assert resp.status_code == 302 and 'login' in resp.headers['Location']


def test_page_can_be_restricted_again(viewer, monkeypatch):
    monkeypatch.setattr(gap_routes, 'GAP_ANALYSIS_MIN_ROLE', 'manager')
    resp = viewer.get(PAGE)
    assert resp.status_code == 302 and 'login' not in resp.headers['Location']


@pytest.mark.parametrize('path', ['grid', 'kernel', 'biotopes', 'cell/5kmE5300N3070'])
def test_api_requires_login(client, fetch_stub, path):
    assert client.get(f'{BASE}/api/gap-analysis/{path}').status_code == 302
    fetch_stub.assert_not_called()


def test_api_open_to_any_signed_in_user(viewer, fetch_stub):
    assert viewer.get(f'{BASE}/api/gap-analysis/grid').status_code == 200


def test_hub_card_shown_to_signed_in_user(viewer):
    assert b'/analysis/gap-analysis' in viewer.get(f'{BASE}/').data


def test_hub_card_hidden_from_anonymous(client):
    assert b'/analysis/gap-analysis' not in client.get(f'{BASE}/').data


def test_hub_card_shown_to_admin(admin):
    assert b'/analysis/gap-analysis' in admin.get(f'{BASE}/').data


def test_hub_card_shown_to_manager(manager):
    assert b'/analysis/gap-analysis' in manager.get(f'{BASE}/').data


def test_english_page_is_translated(admin):
    html = admin.get('/en/camera-traps/analysis/gap-analysis').get_data(as_text=True)
    assert 'Data gap analysis' in html
    assert 'Аналіз прогалин' not in html


# ─── API behaviour ────────────────────────────────────────────────────────────

def test_grid_api_shape(viewer, fetch_stub):
    resp = viewer.get(f'{BASE}/api/gap-analysis/grid?scale=5000')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['summary']['scale_m'] == 5000
    assert {c['status'] for c in body['cells']} == {'data', 'empty'}
    assert {l['id'] for l in body['locations']} == {1, 2, 3}
    assert body['summary']['species_options'][0]['name'] == 'Козуля'


def test_invalid_parameters_fall_back_to_defaults(viewer, fetch_stub):
    body = viewer.get(f'{BASE}/api/gap-analysis/grid?scale=777&unit=hour&taxon=fungi'
                      '&start_date=2025-02-01&end_date=2024-01-01').get_json()
    assert body['summary']['scale_m'] == 5000 and body['summary']['unit'] == 'week'
    kwargs = fetch_stub.call_args.kwargs
    assert kwargs['taxon'] == 'mammals'
    assert kwargs['start_date'] is None and kwargs['end_date'] is None


def test_user_without_institutions_sees_public_locations_only(viewer, fetch_stub):
    viewer.get(f'{BASE}/api/gap-analysis/grid?qc_exclude=qc_poor_placement')
    args, kwargs = fetch_stub.call_args
    assert 'visibility_level = 0' in args[1]
    assert kwargs['qc_exclude'] == ['qc_poor_placement']


def test_institution_user_sees_own_and_public_locations_only(manager, fetch_stub):
    """A manager of one park sees that park's locations plus the public ones,
    the same rule as everywhere else in the module."""
    with patch.object(gap_routes.ct_access, 'allowed_institution_ids', return_value=[7]):
        manager.get(f'{BASE}/api/gap-analysis/grid')
    args, _ = fetch_stub.call_args
    condition, params = args[1], args[2]
    assert 'visibility_level = 0' in condition
    assert 'institution_id = ANY(:user_inst_ids)' in condition
    assert params['user_inst_ids'] == [7]


def test_admin_gets_everything(admin, fetch_stub):
    admin.get(f'{BASE}/api/gap-analysis/grid')
    args, _ = fetch_stub.call_args
    assert args[1].strip().startswith('1=1')


def test_cache_reuses_data_across_layers(viewer, fetch_stub):
    viewer.get(f'{BASE}/api/gap-analysis/grid?scale=5000')
    viewer.get(f'{BASE}/api/gap-analysis/grid?scale=10000')
    viewer.get(f'{BASE}/api/gap-analysis/biotopes')
    viewer.get(f'{BASE}/api/gap-analysis/kernel')
    assert fetch_stub.call_count == 1
    viewer.get(f'{BASE}/api/gap-analysis/grid?taxon=birds')
    assert fetch_stub.call_count == 2


def test_cache_is_keyed_on_access_rights(viewer, fetch_stub):
    """Users with different institution access never share cached data.

    Simulated at the source, see the module docstring on switching users.
    """
    viewer.get(f'{BASE}/api/gap-analysis/grid')
    with patch.object(gap_routes.ct_access, 'allowed_institution_ids', return_value=[5]):
        viewer.get(f'{BASE}/api/gap-analysis/grid')
    assert fetch_stub.call_count == 2


def test_focus_species(viewer, fetch_stub):
    body = viewer.get(f'{BASE}/api/gap-analysis/grid?scale=5000&species_id=11').get_json()
    assert body['summary']['focus']['name'] == 'Рись'
    data_cells = [c for c in body['cells'] if c['status'] == 'data']
    assert all('focus_confidence' in c for c in data_cells)


def test_cell_detail(viewer, fetch_stub):
    body = viewer.get(f'{BASE}/api/gap-analysis/cell/5kmE5300N3070?scale=5000').get_json()
    assert body['S_obs'] == 3 and body['curve']
    assert viewer.get(f'{BASE}/api/gap-analysis/cell/1kmE0N0?scale=5000').status_code == 404


def test_kernel_bandwidth_whitelist(viewer, fetch_stub):
    assert viewer.get(f'{BASE}/api/gap-analysis/kernel?bandwidth=5000').get_json()['bandwidth_m'] == 5000
    assert viewer.get(f'{BASE}/api/gap-analysis/kernel?bandwidth=3').get_json()['bandwidth_m'] == 2500


def test_biotopes(viewer, fetch_stub):
    rows = viewer.get(f'{BASE}/api/gap-analysis/biotopes').get_json()['rows']
    assert {r['name'] for r in rows} == {'Ліс', 'Лука'}


def test_server_error_is_json_500(viewer):
    gap_routes._gap_cache.clear()
    with patch.object(gap_routes.ga, 'fetch_gap_data', side_effect=RuntimeError('boom')), \
         patch.object(gap_routes, 'get_ct_session', return_value=MagicMock()), \
         patch.object(gap_routes, 'close_ct_session') as close:
        resp = viewer.get(f'{BASE}/api/gap-analysis/grid')
    assert resp.status_code == 500 and resp.get_json() == {'error': 'Server error'}
    close.assert_called_once()
