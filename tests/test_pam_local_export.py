# SPDX-License-Identifier: AGPL-3.0-only
"""Local export on the PAM sample-upload page (destination = local folder).

The page can now also go the other way: plan detections or whole recordings of
several species, optionally only the verified ones, and let the browser copy
or cut them into a local folder. These tests pin two things:

* the export switches change the sampling SQL only when asked, so the server
  upload (and every page that deep-links into it) keeps its exact query;
* the prepare endpoint validates and forwards the export request, and answers
  a request without ``destination`` exactly as before.

Run:
    venv/Scripts/python -m pytest tests/test_pam_local_export.py -v
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.pam import pam_segment_sampling as pss


# ── build_sampling_query: export switches ────────────────────────────────────

def test_default_query_is_the_historical_one():
    sql = str(pss.build_sampling_query(10))
    assert 'WITH candidates AS' in sql
    assert 'NOT EXISTS' in sql
    assert 'detection_verification_map' not in sql
    assert 'DISTINCT ON' not in sql
    assert 'consensus' not in sql


def test_export_without_dedup_drops_not_exists_only():
    sql = str(pss.build_sampling_query(10, skip_sampled=False))
    assert 'NOT EXISTS' not in sql
    assert 'detection_verification_map' not in sql
    assert 'ntile(10) OVER (ORDER BY confidence)' in sql


@pytest.mark.parametrize(('verification', 'needle'), [
    ('unverified', 'dvm.verification_result IS NULL AND COALESCE(sv.votes, 0) = 0'),
    ('verified', '(dvm.verification_result IS NOT NULL OR COALESCE(sv.votes, 0) > 0)'),
    ('partial', 'dvm.verification_result IS NULL AND COALESCE(sv.votes, 0) > 0'),
    ('full', 'AND dvm.verification_result IS NOT NULL'),
])
def test_verification_filters(verification, needle):
    sql = str(pss.build_sampling_query(10, skip_sampled=False, verification=verification))
    assert 'LEFT JOIN detection_verification_map dvm' in sql
    assert needle in sql
    assert 'dvm.verification_result AS consensus' in sql


def test_any_verification_joins_votes_without_filtering():
    sql = str(pss.build_sampling_query(10, skip_sampled=False, verification='any'))
    assert 'LEFT JOIN detection_verification_map dvm' in sql
    assert 'COALESCE(sv.votes, 0) = 0' not in sql
    assert 'dvm.verification_result IS NOT NULL' not in sql


@pytest.mark.parametrize(('verdict', 'needle'), [
    ('confirmed', 'dvm.verification_result = 1 OR'),
    ('rejected', 'dvm.verification_result = 0 OR'),
])
def test_verdict_filters(verdict, needle):
    sql = str(pss.build_sampling_query(10, skip_sampled=False,
                                       verification='verified', verdict=verdict))
    assert needle in sql


def test_votes_count_every_model():
    """A verified call is a fact about the animal, not about one model."""
    sql = str(pss.build_sampling_query(10, skip_sampled=False, verification='full'))
    tally = sql.split('LEFT JOIN (')[1].split(') sv')[0]
    assert 'model_id' not in tally


def test_per_recording_stratifies_one_row_per_file():
    sql = str(pss.build_sampling_query(10, skip_sampled=False, per_recording=True))
    assert 'SELECT DISTINCT ON (recording_id) *' in sql
    assert 'ORDER BY recording_id, confidence DESC, detection_id' in sql
    assert 'FROM best' in sql


def test_export_shape_keeps_the_other_filters():
    sql = str(pss.build_sampling_query(
        10, conf_column='conf_perch_v2', with_conf_max=True, month_mode='wrap',
        with_year_range=True, with_date_range=True, skip_sampled=False,
        verification='verified'))
    for needle in ('d.conf_perch_v2 <= :conf_max', 'make_date(:year_start, 1, 1)',
                   'CAST(:date_from AS date)', '>= :month_start OR'):
        assert needle in sql


@pytest.mark.parametrize('kwargs', [
    {'verification': 'everything'},
    {'verdict': 'maybe'},
])
def test_unknown_export_values_are_refused(kwargs):
    with pytest.raises(ValueError):
        pss.build_sampling_query(10, **kwargs)


# ── run_stratified_sample: vote fields in the plan ──────────────────────────

def _rows_conn(rows):
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.fetchall.return_value = rows
    return conn


def _row(**extra):
    row = {'detection_id': 1, 'recording_id': 10, 'species_id': 5,
           'rec_filename': 'STAVY_20250303_184602.wav',
           'datetime_start': datetime(2025, 3, 3, 18, 46, 2), 'location_id': 7,
           'location_name': 'Ставки', 'start_s': 216, 'end_s': 219, 'confidence': 0.5}
    row.update(extra)
    return row


def test_export_plan_carries_the_vote_tally():
    conn = _rows_conn([_row(consensus=None, votes=3, positive=2)])
    plan = pss.run_stratified_sample('Bufo bufo', [7], conn=conn, model_id=1,
                                     skip_sampled=False, verification='verified')
    assert plan[0]['votes'] == 3 and plan[0]['positive_votes'] == 2
    assert plan[0]['consensus'] is None
    assert plan[0]['verification_state'] == 'partial_confirmed'
    sql = str(conn.execute.call_args.args[0])
    assert 'NOT EXISTS' not in sql


def test_server_plan_has_no_vote_fields():
    plan = pss.run_stratified_sample('Bufo bufo', [7], conn=_rows_conn([_row()]), model_id=1)
    assert 'verification_state' not in plan[0]


@pytest.mark.parametrize(('consensus', 'votes', 'positive', 'state'), [
    (1, 2, 2, 'confirmed'),
    (0, 2, 0, 'rejected'),
    (None, 0, 0, 'unverified'),
    (None, 1, 1, 'partial_confirmed'),
    (None, 1, 0, 'partial_rejected'),
    (None, 2, 1, 'partial_tied'),
])
def test_verification_state(consensus, votes, positive, state):
    assert pss.verification_state(consensus, votes, positive) == state


# ── /api/pam/sample/prepare with destination=local ──────────────────────────

def _prepare(client, **body):
    payload = {'species_name': 'Bufo bufo', 'location_ids': [10]}
    payload.update(body)
    return client.post('/uk/api/pam/sample/prepare', json=payload)


def _patches(return_value=None):
    sampler = patch('app.pam.routes.run_stratified_sample',
                    side_effect=lambda name, *a, **k: [{'detection_id': 1, 'species_id': 5}]
                    if return_value is None else return_value)
    return (patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()),
            patch('app.pam.routes._resolve_model_choice', return_value=(1, True)),
            patch('app.pam.utils.get_model_conf_columns', return_value={1: 'confidence'}),
            sampler)


def test_local_export_plans_each_species_without_dedup(auth_client):
    cl = auth_client(role='admin')
    db, model, cols, sampler = _patches()
    with db, model, cols, sampler as sample:
        resp = _prepare(cl, destination='local', species_name='',
                        species_names=['Bufo bufo', 'Rana temporaria', 'Bufo bufo'],
                        verification='full', verdict='confirmed', unit='files',
                        sample_size=30)
    data = resp.get_json()
    assert resp.status_code == 200, data
    assert [c.args[0] for c in sample.call_args_list] == ['Bufo bufo', 'Rana temporaria']
    kwargs = sample.call_args.kwargs
    assert kwargs['skip_sampled'] is False
    assert kwargs['verification'] == 'full' and kwargs['verdict'] == 'confirmed'
    assert kwargs['per_recording'] is True
    assert kwargs['sample_size'] == 30
    assert data['destination'] == 'local' and data['unit'] == 'files'
    assert [s['species_name'] for s in data['segments']] == ['Bufo bufo', 'Rana temporaria']
    assert data['per_species'] == [{'species_name': 'Bufo bufo', 'count': 1},
                                   {'species_name': 'Rana temporaria', 'count': 1}]


def test_local_export_defaults(auth_client):
    cl = auth_client(role='admin')
    db, model, cols, sampler = _patches()
    with db, model, cols, sampler as sample:
        resp = _prepare(cl, destination='local')
    assert resp.status_code == 200
    kwargs = sample.call_args.kwargs
    assert (kwargs['verification'], kwargs['verdict'], kwargs['per_recording']) == ('any', 'any', False)


def test_request_without_destination_is_the_server_upload(auth_client):
    """Deep links (species dashboard, co-occurrence map, priorities) send no
    destination: the sampler must get exactly the historical arguments."""
    cl = auth_client(role='admin')
    db, model, cols, sampler = _patches()
    with db, model, cols, sampler as sample:
        resp = _prepare(cl, species_names=['Rana temporaria'], unit='files',
                        verification='full')
    data = resp.get_json()
    assert resp.status_code == 200
    assert sample.call_count == 1 and sample.call_args.args[0] == 'Bufo bufo'
    for key in ('skip_sampled', 'verification', 'verdict', 'per_recording'):
        assert key not in sample.call_args.kwargs
    assert set(data) == {'success', 'count', 'model_id', 'segments'}


@pytest.mark.parametrize('body', [
    {'verification': 'all'},
    {'verdict': 'yes'},
    {'unit': 'bytes'},
    {'species_names': 'Bufo bufo'},
    {'species_names': [1, 2]},
])
def test_local_export_rejects_bad_values(auth_client, body):
    cl = auth_client(role='admin')
    db, model, cols, sampler = _patches()
    with db, model, cols, sampler as sample:
        resp = _prepare(cl, destination='local', **body)
    assert resp.status_code == 400
    sample.assert_not_called()


def test_local_export_caps_the_species_count(auth_client):
    from app.pam.routes import MAX_EXPORT_SPECIES
    cl = auth_client(role='admin')
    db, model, cols, sampler = _patches()
    with db, model, cols, sampler as sample:
        resp = _prepare(cl, destination='local',
                        species_names=[f'Species {i}' for i in range(MAX_EXPORT_SPECIES + 1)])
    assert resp.status_code == 400
    sample.assert_not_called()


def test_local_export_checks_every_species_access(auth_client):
    cl = auth_client(role='manager', manages=True)
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [SimpleNamespace(location_id=10)]
    allowed = {'Bufo bufo': True, 'Myotis myotis': False}
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes._sample_species_allowed',
               side_effect=lambda _c, name, **k: allowed[name]), \
         patch('app.pam.routes.run_stratified_sample', return_value=[]) as sample:
        resp = _prepare(cl, destination='local', species_names=['Bufo bufo', 'Myotis myotis'])
    assert resp.status_code == 403
    sample.assert_not_called()


# ── the page ─────────────────────────────────────────────────────────────────

def _render(cl, path='/uk/pam/verification/sample-upload'):
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.fetchall.return_value = []
    conn.execute.return_value.fetchall.return_value = []
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get(path)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_page_defaults_to_the_server_with_whole_files_locked(auth_client):
    html = _render(auth_client(role='admin'))
    assert 'id="su-dest-server" value="server" checked' in html
    assert 'id="su-dest-local" value="local">' in html
    assert 'id="su-unit-files" value="files" disabled data-locked="true"' in html
    assert 'id="su-unit-segments" value="segments" checked' in html
    assert 'js/local_export.js' in html
    assert 'const MAX_EXPORT_SPECIES = 20;' in html


def test_local_controls_start_hidden(auth_client):
    import re
    html = _render(auth_client(role='admin'))
    tags = re.findall(r'<[a-z]+[^>]*class="[^"]*su-local-only[^"]*"[^>]*>', html)
    assert len(tags) >= 8
    assert all(re.search(r'\shidden[\s>]', tag) for tag in tags), tags


def test_deep_link_still_lands_on_the_server_mode(auth_client):
    html = _render(auth_client(role='admin'),
                   '/uk/pam/verification/sample-upload?source=priorities&species=Bufo%20bufo')
    assert 'id="su-dest-server" value="server" checked' in html
    assert 'applyPrefill' in html


def test_english_page_translates_the_export_controls(auth_client):
    html = _render(auth_client(role='admin'), '/en/pam/verification/sample-upload')
    for text in ('To the server, for verification', 'To a local folder', 'Whole files',
                 'Partly or fully verified', 'Limit per species', 'Confirm and copy'):
        assert text in html, text
