# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the automated confidence-stratified segment sampling / upload path.

Covers pure helpers (filename build/parse, SQL builder), the sampling driver and
segment registration with mocked connections, real WAV→FLAC encoding via
soundfile, and route tests with the PAM connection patched
at the routes module (mirrors tests/test_pam_import.py's convention).
"""
import io
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

from app.pam import pam_segment_sampling as pss


# ── build_segment_filename ─────────────────────────────────────────────────────

def test_build_segment_filename_canonical():
    # Canonical recording stem is reused verbatim; confidence → 3 decimals;
    # start rounded to an int second; default ext .flac.
    name = pss.build_segment_filename(0.1181, 'STAVY_20250303_184602.wav', 216.0, 1)
    assert name == '0.118_STAVY_20250303_184602_sec216_part1.flac'


def test_build_segment_filename_ext_and_part():
    name = pss.build_segment_filename(0.8, 'K1_20241110_074902.flac', 12, 3, ext='wav')
    assert name == '0.800_K1_20241110_074902_sec12_part3.wav'


def test_build_segment_filename_noncanonical_synthesises_from_datetime():
    # A recording name that is not LOCATION_DATE_TIME: synthesise a canonical
    # stem from a sanitised location token + datetime so the name still parses.
    dt = datetime(2025, 3, 3, 18, 46, 2)
    name = pss.build_segment_filename(
        0.5, 'weird file (1).wav', 30, 1,
        location_name='Ставки, головні', datetime_start=dt)
    # location sanitised to [A-Za-z0-9-]; the result matches the legacy/new regex.
    assert pss._SEG_NEW_RE.match(name)
    assert '_20250303_184602_sec30_part1.flac' in name


# ── parse_segment_filename_for_backfill ─────────────────────────────────────────

def test_parse_new_format():
    p = pss.parse_segment_filename_for_backfill(
        '0.118_STAVY_20250303_184602_sec216_part1.flac')
    assert p == {'recording_stem': 'STAVY_20250303_184602',
                 'start_s': 216, 'confidence': 0.118}


def test_parse_legacy_format_no_startsec():
    p = pss.parse_segment_filename_for_backfill('0.804_K1_20241110_074902.wav')
    assert p['recording_stem'] == 'K1_20241110_074902'
    assert p['start_s'] is None
    assert p['confidence'] == 0.804


def test_parse_unparseable_returns_none():
    assert pss.parse_segment_filename_for_backfill('garbage.wav') is None
    assert pss.parse_segment_filename_for_backfill('notaudio.txt') is None


# ── build_sampling_query ────────────────────────────────────────────────────────

def test_build_sampling_query_interpolates_strata_and_binds():
    sql = str(pss.build_sampling_query(7))
    assert 'ntile(7)' in sql
    # dedup + all user-driven values stay bound params
    assert 'NOT EXISTS' in sql
    for bind in (':species_name', ':location_ids', ':conf_thr', ':per_stratum'):
        assert bind in sql


def test_build_sampling_query_clamps_strata_minimum():
    assert 'ntile(1)' in str(pss.build_sampling_query(0))


def test_build_sampling_query_defaults_to_the_reference_column():
    # Legacy behaviour must be reproduced exactly for BirdNET 2.4.
    sql = str(pss.build_sampling_query(5))
    assert 'd.confidence' in sql
    assert 'detection_models' not in sql  # link table dropped in migration 0007


def test_build_sampling_query_stratifies_by_the_given_model_column():
    # Since migration 0006 every model's score is a column on `detections`, so
    # one code path serves all models - no join, no is_reference branch.
    sql = str(pss.build_sampling_query(5, conf_column='conf_perch_v2'))
    assert 'd.conf_perch_v2' in sql
    assert 'ntile(5) OVER (ORDER BY d.conf_perch_v2)' in sql
    assert 'JOIN detection_models' not in sql


@pytest.mark.parametrize('bad', [
    'conf; DROP TABLE detections--',
    'conf_perch_v2, (SELECT 1)',
    'Confidence',      # uppercase cannot be a real conf_column
    '1conf',
    '',
    None,
])
def test_build_sampling_query_rejects_unsafe_column_names(bad):
    # conf_column is interpolated into SQL text, so anything that did not come
    # from models.conf_column must be refused rather than quoted-and-hoped.
    with pytest.raises(ValueError):
        pss.build_sampling_query(5, conf_column=bad)


def test_run_stratified_sample_passes_the_column_through():
    conn = _mock_conn_returning([])
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn, model_id=2,
                              conf_column='conf_perch_v2')
    sql = str(conn.execute.call_args[0][0])
    assert 'd.conf_perch_v2' in sql
    # The model is only tagged onto the sample; it is no longer a bind param
    # used for scoring, because the column already identifies the model.
    params = conn.execute.call_args[0][1]
    assert params['seg_model_id'] == 2
    assert 'model_id' not in params


# ── run_stratified_sample ───────────────────────────────────────────────────────

def _mock_conn_returning(rows):
    conn = MagicMock()
    res = MagicMock()
    res.mappings.return_value.fetchall.return_value = rows
    conn.execute.return_value = res
    return conn


def test_run_stratified_sample_shapes_rows_and_counts_parts():
    dt = datetime(2025, 3, 3, 18, 46, 2)
    rows = [
        {'detection_id': 1, 'recording_id': 10, 'species_id': 5,
         'rec_filename': r'F:\Archive\STAVY_20250303_184602.wav', 'datetime_start': dt,
         'location_name': 'Ставки', 'start_s': 216, 'end_s': 219, 'confidence': 0.12},
        {'detection_id': 2, 'recording_id': 10, 'species_id': 5,
         'rec_filename': r'F:\Archive\STAVY_20250303_184602.wav', 'datetime_start': dt,
         'location_name': 'Ставки', 'start_s': 300, 'end_s': 303, 'confidence': 0.90},
    ]
    conn = _mock_conn_returning(rows)
    out = pss.run_stratified_sample('Bufo bufo', [10], conn=conn)

    assert len(out) == 2
    assert out[0]['recording_filename'] == 'STAVY_20250303_184602.wav'
    # same recording → part1, part2
    assert out[0]['segment_filename'] == '0.120_STAVY_20250303_184602_sec216_part1.flac'
    assert out[1]['segment_filename'] == '0.900_STAVY_20250303_184602_sec300_part2.flac'
    # location token derived from the canonical recording stem, not the raw name
    assert out[0]['location_name'] == 'STAVY'
    assert out[0]['recorded_date'] == '2025-03-03'
    assert out[0]['detection_id'] == 1 and out[0]['recording_id'] == 10


def test_run_stratified_sample_empty_locations_short_circuits():
    assert pss.run_stratified_sample('Bufo bufo', [], conn=MagicMock()) == []


# ── convert_wav_bytes_to_flac (real encode) ─────────────────────────────────────

def test_convert_wav_bytes_to_flac_roundtrip(tmp_path):
    sr = 16000
    sig = (0.2 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr)).astype('float32')
    wav_buf = io.BytesIO()
    sf.write(wav_buf, sig, sr, format='WAV', subtype='PCM_16')
    wav_bytes = wav_buf.getvalue()

    flac_path = tmp_path / 'seg' / 'clip.flac'
    pss.convert_wav_bytes_to_flac(wav_bytes, str(flac_path))

    assert flac_path.exists()
    data, out_sr = sf.read(str(flac_path))
    assert out_sr == sr
    assert len(data) == sr  # 1 second preserved


# ── register_sampled_segment ────────────────────────────────────────────────────

def _register_kwargs():
    return dict(species_id=5, detection_id=42, recording_id=10,
                segment_filename='0.120_STAVY_20250303_184602_sec216_part1.flac',
                confidence=0.12, location_name='STAVY',
                recorded_date='2025-03-03', recorded_time='18:46:02',
                file_path='/x/seg.flac')


def test_register_sampled_segment_inserts_and_returns_id(app):
    conn = MagicMock()

    def _ex(sql, params=None):
        s = str(sql)
        res = MagicMock()
        if 'INSERT INTO segments' in s:
            res.fetchone.return_value = (555,)
        else:  # dup-check SELECT
            res.fetchone.return_value = None
        return res
    conn.execute.side_effect = _ex

    with app.app_context():
        seg_id = pss.register_sampled_segment(conn, **_register_kwargs())
    assert seg_id == 555


def test_register_sampled_segment_skips_duplicate(app):
    conn = MagicMock()

    def _ex(sql, params=None):
        res = MagicMock()
        if 'INSERT INTO segments' in str(sql):
            raise AssertionError("INSERT must not run for a duplicate")
        res.fetchone.return_value = (1,)  # dup exists
        return res
    conn.execute.side_effect = _ex

    with app.app_context():
        assert pss.register_sampled_segment(conn, **_register_kwargs()) is None


# ── route smoke tests (admin-gated) ─────────────────────────────────────────────

def _mapping_result(rows):
    res = MagicMock()
    res.mappings.return_value.fetchall.return_value = rows
    res.fetchall.return_value = rows
    return res


@pytest.mark.parametrize('role', ['manager', 'pam_verifier'])
def test_sample_upload_page_requires_pam_management(auth_client, role):
    cl = auth_client(role=role)
    resp = cl.get('/uk/pam/verification/sample-upload')
    assert resp.status_code == 403


def test_sample_routes_reject_manager_with_view_only_pam_access(auth_client, db_session):
    from app.models import User

    cl = auth_client(role='manager', manages=True)
    link = User.query.filter_by(username='test_manager').one().institution_links[0]
    link.can_manage_pam = False
    db_session.commit()

    for path in ('/uk/pam/verification/sample-upload',
                 '/uk/pam/verification/segment-window',
                 '/uk/api/pam/sample/species'):
        assert cl.get(path).status_code == 403
    for endpoint in ('prepare', 'plan-window', 'upload-segment'):
        assert cl.post(f'/uk/api/pam/sample/{endpoint}').status_code == 403


def test_sample_upload_page_renders_for_admin(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    conn.execute.return_value = _mapping_result([])
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/pam/verification/sample-upload')
    assert resp.status_code == 200
    assert b'sample/prepare' in resp.data or b'su-prepare' in resp.data
    assert b'id="su-use-index" checked' in resp.data
    assert b'id="su-search-archive" hidden' in resp.data
    assert '<summary>Як працює локальний індекс?</summary>' in resp.get_data(as_text=True)


def test_manager_sample_page_lists_only_managed_institutions(auth_client, db_session):
    from app.models import Institution

    cl = auth_client(role='manager', manages=True)
    managed = Institution.query.filter_by(code='T-test_manager').one()
    db_session.add(Institution(name_uk='Outside PAM scope', code='outside'))
    db_session.commit()
    conn = MagicMock()
    conn.execute.return_value = _mapping_result([])
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes.get_earliest_recording_year', return_value=2020):
        resp = cl.get('/uk/pam/verification/sample-upload')
    assert resp.status_code == 200
    assert managed.name_uk in resp.get_data(as_text=True)
    assert 'Outside PAM scope' not in resp.get_data(as_text=True)
    assert conn.execute.call_args_list[0].args[1] == {
        'is_admin': False, 'managed_ids': [managed.id],
    }


@pytest.mark.parametrize('endpoint', ['prepare', 'plan-window'])
def test_manager_sample_plan_filters_to_managed_locations(auth_client, endpoint):
    cl = auth_client(role='manager', manages=True)
    conn = MagicMock()
    locations = _mapping_result([SimpleNamespace(location_id=10)])
    conn.execute.side_effect = [locations, MagicMock()]
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes._resolve_model_choice', return_value=(1, True)), \
         patch('app.pam.utils.get_model_conf_columns', return_value={1: 'confidence'}), \
         patch('app.pam.routes.run_stratified_sample', return_value=[]) as sample, \
         patch('app.pam.pam_segment_sampling.plan_window_segments', return_value=[]) as window:
        resp = cl.post(f'/uk/api/pam/sample/{endpoint}', json={
            'location_ids': [10, 11], 'species_name': 'Bufo bufo',
            'from_ts': '2025-03-03T18:00:00Z', 'to_ts': '2025-03-03T19:00:00Z',
        })
    assert resp.status_code == 200
    if endpoint == 'prepare':
        assert sample.call_args.args[1] == [10]
    else:
        assert window.call_args.kwargs['location_ids'] == [10]


@pytest.mark.parametrize(('source_found', 'managed_location', 'filename', 'status'), [
    (True, True, '0.120_STAVY_20250303_184602_sec216_part1.flac', 200),
    (True, False, '0.120_STAVY_20250303_184602_sec216_part1.flac', 403),
    (False, True, '0.120_STAVY_20250303_184602_sec216_part1.flac', 403),
    (True, True, '0.120_OTHER_20250303_184602_sec216_part1.flac', 400),
])
def test_manager_upload_validates_source_and_location(
    auth_client, source_found, managed_location, filename, status,
):
    cl = auth_client(role='manager', manages=True)
    conn = MagicMock()
    lookup = MagicMock()
    lookup.fetchone.return_value = SimpleNamespace(
        location_id=10, filename='STAVY_20250303_184602.wav',
        location_name='Стави', scientific_name='Bufo bufo', confidence=0.12,
        start_s=216, datetime_start=datetime(2025, 3, 3, 18, 46, 2),
    ) if source_found else None
    scope = MagicMock()
    scope.fetchone.return_value = (1,) if managed_location else None
    conn.execute.side_effect = [lookup, scope]
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes._resolve_model_choice', return_value=(1, True)), \
         patch('app.pam.utils.get_model_conf_columns', return_value={1: 'confidence'}), \
         patch('app.pam.routes.save_and_register_segment', return_value=('saved', 777)) as save:
        resp = cl.post('/uk/api/pam/sample/upload-segment', data={
            'segment': (io.BytesIO(b'RIFFxxxxWAVE'), 'clip.wav'),
            'species_name': 'Client-supplied name', 'species_id': '5',
            'detection_id': '42', 'recording_id': '10',
            'segment_filename': filename, 'location_name': 'OTHER',
        }, content_type='multipart/form-data')
    assert resp.status_code == status
    params = conn.execute.call_args_list[0].args[1]
    assert (params['detection_id'], params['recording_id'], params['species_id']) == (42, 10, 5)
    if status == 200:
        save.assert_called_once()
        assert save.call_args.args[1]['location_name'] == 'STAVY'
        assert save.call_args.args[1]['species_name'] == 'Bufo bufo'
        assert save.call_args.kwargs['isolate_file'] is True
    else:
        save.assert_not_called()


def test_api_sample_species_returns_mapped_list(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    conn.execute.return_value = _mapping_result([
        {'species_id': 5, 'scientific_name': 'Bufo bufo',
         'common_name_uk': 'Ропуха сіра', 'common_name_en': 'Common toad'},
    ])
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/api/pam/sample/species?location_ids=10,11')
    body = json.loads(resp.data)
    assert resp.status_code == 200
    assert body['species'][0]['scientific_name'] == 'Bufo bufo'
    assert 'Ропуха сіра' in body['species'][0]['text']


def test_api_sample_prepare_returns_segments(auth_client):
    cl = auth_client(role='admin')
    fake = [{'detection_id': 1, 'recording_id': 10, 'segment_filename': 'x.flac'}]
    conn = MagicMock()
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes.run_stratified_sample', return_value=fake) as m:
        resp = cl.post('/uk/api/pam/sample/prepare',
                       data=json.dumps({'species_name': 'Bufo bufo',
                                        'location_ids': [10]}),
                       content_type='application/json')
    body = json.loads(resp.data)
    assert resp.status_code == 200
    assert body['success'] and body['count'] == 1
    m.assert_called_once()


def test_recording_locations_respects_manager_scope(auth_client):
    client = auth_client(role='manager', manages=True)
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value = [
        {'filename': 'one.wav', 'location_id': 11, 'lat': 49.9, 'lon': 23.7},
    ]
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes._sample_managed_location_ids', return_value=[11]):
        response = client.post('/uk/api/pam/sample/recording-locations',
                               json={'filenames': ['one.wav', 'one.wav', 'two.wav']})
    assert response.status_code == 200
    assert response.json == {
        'locations': {'one.wav': {'location_id': 11, 'lat': 49.9, 'lon': 23.7}},
    }
    statement, params = conn.execute.call_args.args
    assert set(params['names']) == {'one.wav', 'two.wav'}
    assert params['managed'] == [11] and params['admin'] is False
    assert 'count(DISTINCT location_id) = 1' in str(statement)
    assert 'replace(r.filename, chr(92)' in str(statement)
    conn.close.assert_called_once()


def test_recording_locations_rejects_paths(auth_client):
    client = auth_client(role='admin')
    response = client.post('/uk/api/pam/sample/recording-locations',
                           json={'filenames': ['folder/one.wav']})
    assert response.status_code == 400


def test_api_sample_prepare_missing_fields_400(auth_client):
    cl = auth_client(role='admin')
    resp = cl.post('/uk/api/pam/sample/prepare',
                   data=json.dumps({'species_name': ''}),
                   content_type='application/json')
    assert resp.status_code == 400


def test_api_sample_upload_segment_saves(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes.save_and_register_segment', return_value=('saved', 777)):
        resp = cl.post('/uk/api/pam/sample/upload-segment', data={
            'segment': (io.BytesIO(b'RIFFxxxxWAVE'), 'clip.wav'),
            'species_name': 'Bufo bufo', 'species_id': '5',
            'detection_id': '42', 'recording_id': '10',
            'segment_filename': '0.120_STAVY_20250303_184602_sec216_part1.flac',
            'confidence': '0.12', 'location_name': 'STAVY',
        }, content_type='multipart/form-data')
    body = json.loads(resp.data)
    assert resp.status_code == 200
    assert body['success'] and body['status'] == 'saved' and body['segment_id'] == 777


def test_api_sample_upload_segment_missing_metadata_400(auth_client):
    cl = auth_client(role='admin')
    resp = cl.post('/uk/api/pam/sample/upload-segment', data={
        'segment': (io.BytesIO(b'RIFFxxxxWAVE'), 'clip.wav'),
        'species_name': 'Bufo bufo',  # missing species_id/detection_id/filename
    }, content_type='multipart/form-data')
    assert resp.status_code == 400
