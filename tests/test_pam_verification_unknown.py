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


def _post_submit(auth_client, result, conn):
    cl = auth_client(role='pam_verifier')
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
    cl = auth_client(role='pam_verifier')
    conn = MagicMock()
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.post('/uk/api/verification/submit',
                       data=json.dumps({'segment_id': 42, 'verification_result': 5}),
                       content_type='application/json')
    assert resp.status_code == 400


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
