# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the "don't know" verification vote, auto-discard, and the
institution filter on the verification queue."""
import json
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.pam.routes import _parse_id_list


# ── _parse_id_list ─────────────────────────────────────────────────────────────

def test_parse_id_list_basic():
    assert _parse_id_list('1,2,3') == [1, 2, 3]
    assert _parse_id_list('') == []
    assert _parse_id_list(None) == []
    assert _parse_id_list('1, x, 3,') == [1, 3]


# ── submit: "don't know" (result=2) + auto-discard ──────────────────────────────

def _submit_conn(unknown_votes, meaningful_votes, existing=None):
    """Mock PAM connection for the submit route."""
    conn = MagicMock()

    def _ex(sql, params=None):
        s = str(sql)
        res = MagicMock()
        if 'SELECT id, status FROM segments' in s:
            res.fetchone.return_value = (params['segment_id'], 'pending')
        elif 'SELECT id FROM segment_verifications' in s:
            res.fetchone.return_value = existing
        elif 'FILTER (WHERE verification_result = 2)' in s:
            res.fetchone.return_value = SimpleNamespace(
                unknown_votes=unknown_votes, meaningful_votes=meaningful_votes)
        else:
            res.fetchone.return_value = None
        return res

    conn.execute.side_effect = _ex
    return conn


def _post_submit(auth_client, result, conn, role='admin'):
    # Default role='admin' bypasses the institution-access check so these tests
    # exercise the discard logic in isolation (access is covered separately).
    cl = auth_client(role=role)
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        return cl.post('/uk/api/verification/submit',
                       data=json.dumps({'segment_id': 42, 'verification_result': result}),
                       content_type='application/json')


def test_submit_unknown_accepted(auth_client):
    conn = _submit_conn(unknown_votes=1, meaningful_votes=0)
    resp = _post_submit(auth_client, 2, conn)
    body = json.loads(resp.data)
    assert resp.status_code == 200
    assert body['success'] and body['discarded'] is False


def test_submit_unknown_triggers_discard_at_threshold(auth_client):
    conn = _submit_conn(unknown_votes=3, meaningful_votes=0)
    resp = _post_submit(auth_client, 2, conn)
    body = json.loads(resp.data)
    assert resp.status_code == 200 and body['discarded'] is True
    # a discard UPDATE must have been issued
    sqls = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("UPDATE segments SET status = 'discarded'" in s for s in sqls)


def test_submit_unknown_no_discard_if_meaningful_votes_exist(auth_client):
    # 3 unknowns but also a real yes/no vote → must NOT discard
    conn = _submit_conn(unknown_votes=3, meaningful_votes=1)
    resp = _post_submit(auth_client, 2, conn)
    body = json.loads(resp.data)
    assert body['discarded'] is False
    sqls = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert not any('discarded' in s for s in sqls)


def test_submit_rejects_invalid_result(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.post('/uk/api/verification/submit',
                       data=json.dumps({'segment_id': 42, 'verification_result': 5}),
                       content_type='application/json')
    assert resp.status_code == 400


def test_submit_denied_without_institution_access(auth_client):
    # A verifier with NO institutions must not verify any segment (403).
    conn = _submit_conn(unknown_votes=0, meaningful_votes=0)  # access SELECT → None
    resp = _post_submit(auth_client, 1, conn, role='pam_verifier')
    assert resp.status_code == 403


def test_next_segment_applies_access_baseline_for_non_admin(auth_client):
    # Non-admin with no institutions → access baseline denies all ('FALSE').
    cl = auth_client(role='pam_verifier')
    conn = MagicMock()
    captured = {}

    def _ex(sql, params=None):
        captured['sql'] = str(sql)
        res = MagicMock()
        res.fetchone.return_value = None
        return res
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/next-segment')

    assert 'FALSE' in captured['sql']


# ── next-segment: institution filter ────────────────────────────────────────────

def test_next_segment_institution_filter_adds_join(auth_client):
    cl = auth_client(role='pam_verifier')
    conn = MagicMock()
    captured = {}

    def _ex(sql, params=None):
        captured['sql'] = str(sql)
        captured['params'] = params
        res = MagicMock()
        res.fetchone.return_value = None  # 404 path is fine; we inspect the SQL
        return res
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/next-segment?species_id=175345&institution_ids=3,4')

    assert 'location_institutions' in captured['sql']
    assert captured['params']['institution_ids'] == [3, 4]
    assert captured['params']['species_id'] == 175345


def test_next_segment_no_institution_filter_when_absent(auth_client):
    cl = auth_client(role='pam_verifier')
    conn = MagicMock()
    captured = {}

    def _ex(sql, params=None):
        captured['sql'] = str(sql)
        res = MagicMock()
        res.fetchone.return_value = None
        return res
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/next-segment')

    assert 'location_institutions' not in captured['sql']


# ── filter-options (mutual cascade) ─────────────────────────────────────────────

def _mapping_result(rows):
    res = MagicMock()
    res.mappings.return_value.fetchall.return_value = rows
    return res


def test_filter_options_returns_species_and_institutions(auth_client):
    cl = auth_client(role='admin')  # admin: no institution restriction
    conn = MagicMock()

    def _ex(sql, params=None):
        s = str(sql)
        if 'FROM segments seg' in s and 'JOIN species s' in s:
            return _mapping_result([{'species_id': 5, 'scientific_name': 'Bufo bufo',
                                     'common_name_uk': 'Ропуха', 'common_name_en': 'Toad'}])
        return _mapping_result([{'id': 3, 'name_uk': 'Установа А', 'name_en': 'Inst A'}])
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/api/verification/filter-options?institution_ids=3')
    body = json.loads(resp.data)
    assert resp.status_code == 200
    assert body['species'][0]['id'] == 5
    assert body['institutions'][0]['id'] == 3


def test_filter_options_species_query_filtered_by_institution(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    seen = []

    def _ex(sql, params=None):
        seen.append((str(sql), params))
        if 'JOIN species s' in str(sql):
            return _mapping_result([])
        return _mapping_result([])
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/filter-options?institution_ids=3,4')

    # The species option query (not the classes query, which also joins species).
    species_sql = next(s for s, p in seen if 'JOIN species s' in s and 'AS cls' not in s)
    assert 'location_institutions' in species_sql


# ── taxonomic class filter (next-segment, stats, cascade) ───────────────────────

def _class_result(rows):
    """Mock a .fetchall() result of Row-like objects exposing a .cls attribute."""
    res = MagicMock()
    res.fetchall.return_value = [SimpleNamespace(cls=c) for c in rows]
    return res


def test_next_segment_class_filter_adds_condition(auth_client):
    cl = auth_client(role='admin')  # admin bypasses access baseline → clean SQL
    conn = MagicMock()
    captured = {}

    def _ex(sql, params=None):
        captured['sql'] = str(sql)
        captured['params'] = params
        res = MagicMock()
        res.fetchone.return_value = None
        return res
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/next-segment?class_name=Aves')

    assert 's.class = :class_name' in captured['sql']
    assert captured['params']['class_name'] == 'Aves'


def test_next_segment_no_class_condition_when_absent(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    captured = {}

    def _ex(sql, params=None):
        captured['sql'] = str(sql)
        res = MagicMock()
        res.fetchone.return_value = None
        return res
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/next-segment')

    assert 's.class = :class_name' not in captured['sql']


def test_filter_options_returns_classes(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()

    def _ex(sql, params=None):
        s = str(sql)
        if 'AS cls' in s:                       # the classes option query
            return _class_result(['Aves', 'Mammalia'])
        if 'JOIN species s' in s:               # the species option query
            return _mapping_result([{'species_id': 5, 'scientific_name': 'Bufo bufo',
                                     'common_name_uk': 'Ропуха', 'common_name_en': 'Toad'}])
        return _mapping_result([{'id': 3, 'name_uk': 'Установа А', 'name_en': 'Inst A'}])
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/api/verification/filter-options')
    body = json.loads(resp.data)
    assert resp.status_code == 200
    assert [c['id'] for c in body['classes']] == ['Aves', 'Mammalia']
    assert body['classes'][0]['text'] == 'Aves'


def test_filter_options_class_filters_species_and_institutions(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    seen = []

    def _ex(sql, params=None):
        seen.append((str(sql), params))
        if 'AS cls' in str(sql):
            return _class_result([])
        return _mapping_result([])
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        cl.get('/uk/api/verification/filter-options?class_name=Aves')

    # Species options constrained by the chosen class.
    species_sql, sp_params = next((s, p) for s, p in seen
                                  if 'JOIN species s' in s and 'AS cls' not in s)
    assert 's.class = :class_name' in species_sql
    assert sp_params['class_name'] == 'Aves'
    # Institution options constrained by the chosen class (species subquery).
    inst_sql, inst_params = next((s, p) for s, p in seen if 'JOIN institutions i' in s)
    assert 'SELECT species_id FROM species WHERE class = :class_name' in inst_sql
    assert inst_params['class_name'] == 'Aves'


def test_stats_class_filter_applies_subquery(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    seen = []

    def _ex(sql, params=None):
        seen.append((str(sql), params))
        res = MagicMock()
        res.fetchone.return_value = SimpleNamespace(
            total_verifications=0, positive_verifications=0,
            negative_verifications=0, skipped_verifications=0)
        res.scalar.return_value = 0
        res.fetchall.return_value = []
        return res
    conn.execute.side_effect = _ex

    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/api/verification/stats?class_name=Aves')

    assert resp.status_code == 200
    assert any('SELECT species_id FROM species WHERE class = :class_name' in s
               for s, p in seen)
    assert any(p and p.get('class_name') == 'Aves' for s, p in seen)


# ── next-segment: bilingual location from the locations registry ─────────────────

def _next_segment_row(loc_uk, loc_en, seg_loc='FILE_LOC'):
    """A full next-segment result tuple (12 cols): ...seg.location_name(3)...,
    l.location_name(10, uk), l.location_name_en(11, en)."""
    return (
        7, 'seg.wav', 0.912, seg_loc,
        date(2024, 10, 11), time(10, 51, 2), '/path/seg.wav',
        'Pelobates fuscus', 'Часничниця', 'Common spadefoot toad',
        loc_uk, loc_en,
    )


def _next_segment_conn(row):
    conn = MagicMock()

    def _ex(sql, params=None):
        res = MagicMock()
        res.fetchone.return_value = row
        return res
    conn.execute.side_effect = _ex
    return conn


def test_next_segment_location_from_registry_uk(auth_client):
    cl = auth_client(role='admin')
    conn = _next_segment_conn(_next_segment_row('Дніпро', 'Dnipro'))
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/api/verification/next-segment')
    assert json.loads(resp.data)['location_name'] == 'Дніпро'


def test_next_segment_location_from_registry_en(auth_client):
    cl = auth_client(role='admin')
    conn = _next_segment_conn(_next_segment_row('Дніпро', 'Dnipro'))
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/en/api/verification/next-segment')
    assert json.loads(resp.data)['location_name'] == 'Dnipro'


def test_next_segment_location_en_falls_back_to_uk(auth_client):
    # English UI but no English registry name → fall back to the UK registry name.
    cl = auth_client(role='admin')
    conn = _next_segment_conn(_next_segment_row('Дніпро', None))
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/en/api/verification/next-segment')
    assert json.loads(resp.data)['location_name'] == 'Дніпро'


def test_next_segment_location_falls_back_to_filename_when_unlinked(auth_client):
    # No registry link (LEFT JOIN → NULLs) → the filename-parsed name is used.
    cl = auth_client(role='admin')
    conn = _next_segment_conn(_next_segment_row(None, None, seg_loc='DAY'))
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/api/verification/next-segment')
    assert json.loads(resp.data)['location_name'] == 'DAY'
