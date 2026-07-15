# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the automated confidence-stratified segment sampling / upload path.

Covers pure helpers (filename build/parse, SQL builder), the sampling driver and
segment registration with mocked connections, real WAV→FLAC encoding via
soundfile, and route smoke tests (admin-gated) with the PAM connection patched
at the routes module (mirrors tests/test_pam_import.py's convention).
"""
import io
import json
from datetime import datetime
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
         'rec_filename': 'STAVY_20250303_184602.wav', 'datetime_start': dt,
         'location_name': 'Ставки', 'start_s': 216, 'end_s': 219, 'confidence': 0.12},
        {'detection_id': 2, 'recording_id': 10, 'species_id': 5,
         'rec_filename': 'STAVY_20250303_184602.wav', 'datetime_start': dt,
         'location_name': 'Ставки', 'start_s': 300, 'end_s': 303, 'confidence': 0.90},
    ]
    conn = _mock_conn_returning(rows)
    out = pss.run_stratified_sample('Bufo bufo', [10], conn=conn)

    assert len(out) == 2
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


def test_sample_upload_page_requires_admin(auth_client):
    # a non-admin (manager) must not reach the admin page
    cl = auth_client(role='manager')
    resp = cl.get('/uk/pam/verification/sample-upload')
    assert resp.status_code == 403


def test_sample_upload_page_renders_for_admin(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    conn.execute.return_value = _mapping_result([])
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/pam/verification/sample-upload')
    assert resp.status_code == 200
    assert b'sample/prepare' in resp.data or b'su-prepare' in resp.data


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
