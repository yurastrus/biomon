"""Tests for the PAM gap analysis page and its JSON API (app/pam/gap_routes.py).

fetch_gap_data (PostgreSQL-only SQL) is replaced by a stub returning synthetic
GapData; what is checked here is the route layer: access (page and API for every
signed-in user, data limited by institution access and species role), the
confidence threshold, parameter validation, caching keyed on access, JSON shapes.

One user per test: Flask-Login caches the user on ``g`` of the long-lived test
app context, so switching users inside a test silently keeps the first one.

Run:
    venv/Scripts/python -m pytest tests/test_pam_gap_routes.py -v
"""
from unittest.mock import MagicMock, patch

import pytest

from app.pam import gap_routes
from tests.test_pam_gap_analysis import make_raw

PAGE = '/uk/pam/gap-analysis'
API = '/uk/api/pam/gap-analysis'


def _conn():
    conn = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    conn.execute.return_value = result
    return conn


@pytest.fixture
def fetch_stub():
    """Patch the DB layer; yields the mock so tests can inspect its calls."""
    gap_routes._gap_cache.clear()
    stub = MagicMock(side_effect=lambda *a, **kw: make_raw())
    with patch.object(gap_routes.ga, 'fetch_gap_data', stub), \
         patch.object(gap_routes, 'get_pam_db_connection', side_effect=lambda: _conn()), \
         patch.object(gap_routes, 'get_models_list', return_value=[]):
        yield stub
    gap_routes._gap_cache.clear()


@pytest.fixture
def page_db():
    """The page itself reads institutions and models only."""
    with patch.object(gap_routes, 'get_pam_db_connection', side_effect=lambda: _conn()), \
         patch.object(gap_routes, 'get_models_list', return_value=[
             {'model_id': 1, 'label': 'BirdNET 2.4', 'is_reference': True, 'conf_column': 'confidence'},
             {'model_id': 2, 'label': 'Perch v2', 'is_reference': False, 'conf_column': 'conf_perch_v2'}]):
        yield


@pytest.fixture
def viewer(auth_client):
    return auth_client(role='viewer')


@pytest.fixture
def admin(auth_client):
    return auth_client(role='admin')


@pytest.fixture
def manager(auth_client):
    return auth_client(role='manager')


# ─── Access ───────────────────────────────────────────────────────────────────

def test_page_renders_with_threshold_slider(viewer, page_db):
    resp = viewer.get(PAGE)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'gap-map' in html
    assert 'id="threshold-range" min="0.7" max="1.0"' in html
    assert 'step="0.01" value="0.95"' in html
    assert 'Perch v2' in html and 'value="combined"' in html


def test_page_open_to_any_signed_in_user(viewer, page_db):
    assert gap_routes.GAP_ANALYSIS_MIN_ROLE is None
    assert viewer.get(PAGE).status_code == 200


def test_page_needs_login(client):
    resp = client.get(PAGE)
    assert resp.status_code == 302 and 'login' in resp.headers['Location']


def test_page_can_be_restricted_again(viewer, page_db, monkeypatch):
    monkeypatch.setattr(gap_routes, 'GAP_ANALYSIS_MIN_ROLE', 'manager')
    assert viewer.get(PAGE).status_code == 403


@pytest.mark.parametrize('path', ['grid', 'kernel', 'biotopes', 'cell/5kmE5300N3070'])
def test_api_requires_login(client, fetch_stub, path):
    assert client.get(f'{API}/{path}').status_code == 302
    fetch_stub.assert_not_called()


def test_hub_card_shown_to_signed_in_user(viewer):
    assert b'/pam/gap-analysis' in viewer.get('/uk/pam').data


def test_hub_card_hidden_from_anonymous(client):
    assert b'/pam/gap-analysis' not in client.get('/uk/pam').data


def test_english_page_is_translated(admin, page_db):
    html = admin.get('/en/pam/gap-analysis').get_data(as_text=True)
    assert 'Data gap analysis' in html
    assert 'Аналіз прогалин' not in html
    assert 'Confidence threshold' in html


# ─── Access reaching the SQL ──────────────────────────────────────────────────

def test_user_without_institutions_sees_public_locations_only(viewer, fetch_stub):
    viewer.get(f'{API}/grid')
    args, kwargs = fetch_stub.call_args
    assert 'visibility_level = 0' in args[1]
    assert 's.required_role IS NULL' in kwargs['species_condition']


def test_institution_user_sees_own_and_public_locations_only(manager, fetch_stub):
    with patch.object(gap_routes.pam_access, 'allowed_institution_ids', return_value=[7]):
        manager.get(f'{API}/grid?institution_id=7')
    args, kwargs = fetch_stub.call_args
    condition, params = args[1], args[2]
    assert 'visibility_level = 0' in condition
    assert 'institution_id = ANY(:user_inst_ids)' in condition
    assert params['user_inst_ids'] == [7]
    assert params['selected_inst_id'] == [7]
    assert kwargs['species_params'] == {'user_roles': ['manager']}


def test_admin_gets_everything_including_sensitive_species(admin, fetch_stub):
    admin.get(f'{API}/grid')
    args, kwargs = fetch_stub.call_args
    assert args[1].strip().startswith('1=1')
    assert kwargs['species_condition'] == 'TRUE'


# ─── Threshold and parameters ─────────────────────────────────────────────────

def test_threshold_filters_detections(viewer, fetch_stub):
    high = viewer.get(f'{API}/grid?threshold=0.95').get_json()['summary']
    low = viewer.get(f'{API}/grid?threshold=0.7').get_json()['summary']
    assert high['threshold'] == 0.95 and low['threshold'] == 0.7
    assert high['total']['S_obs'] == 2 and low['total']['S_obs'] == 3
    assert fetch_stub.call_count == 1      # the slider never refetches


def test_threshold_is_clamped(viewer, fetch_stub):
    assert viewer.get(f'{API}/grid?threshold=0.2').get_json()['summary']['threshold'] == 0.7
    assert viewer.get(f'{API}/grid?threshold=9').get_json()['summary']['threshold'] == 1.0
    assert viewer.get(f'{API}/grid').get_json()['summary']['threshold'] == 0.95


def test_invalid_parameters_fall_back_to_defaults(viewer, fetch_stub):
    body = viewer.get(f'{API}/grid?scale=777&unit=hour&taxon=fungi&model=xyz'
                      '&start_date=2025-02-01&end_date=2024-01-01').get_json()
    assert body['summary']['scale_m'] == 5000 and body['summary']['unit'] == 'week'
    kwargs = fetch_stub.call_args.kwargs
    assert kwargs['taxon'] == 'birds'
    assert kwargs['start_date'] is None and kwargs['end_date'] is None
    assert kwargs['score_sql'] == 'd.confidence'


def test_model_choice_reaches_score_sql(viewer, fetch_stub):
    with patch('app.pam.utils.get_model_conf_columns',
               return_value={1: 'confidence', 2: 'conf_perch_v2'}):
        viewer.get(f'{API}/grid?model=2')
        assert fetch_stub.call_args.kwargs['score_sql'] == 'd.conf_perch_v2'
        viewer.get(f'{API}/grid?model=combined')
        kwargs = fetch_stub.call_args.kwargs
    assert kwargs['score_sql'].startswith('GREATEST(')
    assert ' OR ' in kwargs['score_filter_sql']


def test_grid_api_shape(viewer, fetch_stub):
    body = viewer.get(f'{API}/grid?scale=5000').get_json()
    assert {c['status'] for c in body['cells']} == {'data', 'empty'}
    assert {l['id'] for l in body['locations']} == {1, 2, 3}
    assert body['summary']['threshold_effect']['species_at_min'] == 3
    assert body['summary']['species_options'][0]['name'] == 'Вільшанка'


def test_cache_is_keyed_on_access_rights(viewer, fetch_stub):
    viewer.get(f'{API}/grid')
    with patch.object(gap_routes.pam_access, 'allowed_institution_ids', return_value=[5]):
        viewer.get(f'{API}/grid')
    assert fetch_stub.call_count == 2


def test_cache_reuses_data_across_layers(viewer, fetch_stub):
    viewer.get(f'{API}/grid?scale=5000')
    viewer.get(f'{API}/grid?scale=10000')
    viewer.get(f'{API}/biotopes')
    viewer.get(f'{API}/kernel')
    assert fetch_stub.call_count == 1
    viewer.get(f'{API}/grid?taxon=amphibians')
    assert fetch_stub.call_count == 2


def test_cell_detail(viewer, fetch_stub):
    body = viewer.get(f'{API}/cell/5kmE5300N3070?scale=5000&threshold=0.7').get_json()
    assert body['S_obs'] == 3 and body['curve'] and len(body['diel_minutes']) == 24
    assert viewer.get(f'{API}/cell/1kmE0N0?scale=5000').status_code == 404


def test_kernel_bandwidth_whitelist(viewer, fetch_stub):
    assert viewer.get(f'{API}/kernel?bandwidth=500').get_json()['bandwidth_m'] == 500
    assert viewer.get(f'{API}/kernel?bandwidth=3').get_json()['bandwidth_m'] == 1000


def test_biotopes(viewer, fetch_stub):
    rows = viewer.get(f'{API}/biotopes').get_json()['rows']
    assert {r['name'] for r in rows} == {'Ліс', 'Лука'}


def test_server_error_is_json_500(viewer):
    gap_routes._gap_cache.clear()
    conn = _conn()
    with patch.object(gap_routes.ga, 'fetch_gap_data', side_effect=RuntimeError('boom')), \
         patch.object(gap_routes, 'get_pam_db_connection', return_value=conn):
        resp = viewer.get(f'{API}/grid')
    assert resp.status_code == 500 and resp.get_json() == {'error': 'Server error'}
    conn.close.assert_called_once()
