# SPDX-License-Identifier: AGPL-3.0-only
"""PAM "flag for re-review" (port of the camera-trap series flag, 2026-11-25).

  * a verifier flags a segment with an optional comment; the author is stored;
  * the admin list shows species, author and comment;
  * only an admin removes the flag;
  * cleanup_completed_verifications never selects flagged segments, so their
    audio survives consensus until the flag is removed.
"""
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pytest


def _conn(found=True, rows=None, rowcount=1):
    conn = MagicMock()

    def _ex(sql, params=None):
        s = str(sql)
        res = MagicMock()
        if 'SELECT seg.id FROM segments seg' in s:
            res.fetchone.return_value = (params['sid'],) if found else None
        elif 'WHERE seg.flagged' in s:
            res.mappings.return_value.fetchall.return_value = rows or []
        elif 'COUNT(*) FROM segments WHERE flagged' in s:
            res.scalar.return_value = len(rows or [])
        else:
            res.rowcount = rowcount
        return res

    conn.execute.side_effect = _ex
    return conn


def _sqls(conn):
    return [(str(c.args[0]), c.args[1] if len(c.args) > 1 else None)
            for c in conn.execute.call_args_list]


def test_verifier_flags_segment_with_author_and_note(auth_client, make_user):
    cl = auth_client(role='admin', username='flagger')
    conn = _conn()
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.post('/uk/api/verification/segment/42/flag', json={'note': '  дві сови  '})
    assert resp.status_code == 200 and resp.json['success']
    update = [(s, p) for s, p in _sqls(conn) if 'SET flagged = TRUE' in s]
    assert update, 'flag UPDATE not issued'
    params = update[0][1]
    assert params['sid'] == 42 and params['note'] == 'дві сови' and params['user_id']
    conn.commit.assert_called_once()


def test_flag_unknown_segment_is_404(auth_client):
    conn = _conn(found=False)
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = auth_client(role='admin').post('/uk/api/verification/segment/7/flag', json={})
    assert resp.status_code == 404
    assert not any('SET flagged = TRUE' in s for s, _ in _sqls(conn))


@pytest.mark.parametrize('role, cleared', [('admin', True), ('pam_verifier', False)])
def test_only_admin_removes_the_flag(auth_client, role, cleared):
    conn = _conn()
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        auth_client(role=role).post('/uk/pam/admin/flagged/42/unflag', data={})
    assert any('SET flagged = FALSE' in s for s, _ in _sqls(conn)) is cleared


def test_admin_list_shows_author_and_note(auth_client, make_user):
    author = make_user(username='sova_fan', roles=('pam_verifier',))
    rows = [{
        'id': 42, 'filename': 'x.flac', 'file_path': None, 'status': 'completed',
        'confidence_level': 0.81, 'location_name': 'R-07',
        'recorded_date': date(2026, 5, 1), 'recorded_time': time(4, 30),
        'verification_count': 2, 'positive_verifications': 2,
        'flag_note': 'можливо, сичик', 'flagged_by': author.id,
        'flagged_at': datetime(2026, 11, 25, 10, 0),
        'scientific_name': 'Strix aluco', 'common_name_uk': 'Сова сіра', 'common_name_en': 'Tawny Owl',
        'loc_name_uk': 'Розточчя 7', 'loc_name_en': None,
    }]
    with patch('app.pam.routes.get_pam_db_connection', return_value=_conn(rows=rows)):
        resp = auth_client(role='admin').get('/uk/pam/admin/flagged')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for text in ('sova_fan', 'можливо, сичик', 'Сова сіра (Strix aluco)', 'Розточчя 7', '/unflag'):
        assert text in html


def test_admin_hub_shows_flagged_badge(auth_client):
    with patch('app.pam.routes.get_pam_db_connection', return_value=_conn(rows=[{}, {}])):
        html = auth_client(role='admin').get('/uk/pam/admin').get_data(as_text=True)
    assert '/uk/pam/admin/flagged' in html and ': 2' in html


def test_cleanup_skips_flagged_segments(app):
    from app.pam import pam_evaluation_utils as peu
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    with app.app_context(), patch.object(peu, 'get_pam_db_connection', return_value=conn):
        assert peu.cleanup_completed_verifications()['success']
    select = str(conn.execute.call_args_list[0].args[0])
    assert "status = 'completed'" in select and 'NOT flagged' in select
