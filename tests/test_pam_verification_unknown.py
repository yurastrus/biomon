# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the "don't know" verification vote, auto-discard, and the
institution filter on the verification queue."""
import json
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

    species_sql = next(s for s, p in seen if 'JOIN species s' in s)
    assert 'location_institutions' in species_sql
