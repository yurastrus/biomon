"""
Idea 7: PAM verification queue -- prioritize segments close to consensus.

GET /api/verification/next-segment must sort candidates by
verification_count DESC (NULL-safe via COALESCE) and only then RANDOM():
segments with contested votes (1:1) and a single vote are served before
fresh ones -- this way consensus is reached faster.

Run:
    venv/Scripts/python -m pytest tests/test_pam_verification_priority.py -v
"""
from datetime import date, time
from unittest.mock import MagicMock, patch

EXPECTED_ORDER_BY = 'ORDER BY COALESCE(seg.verification_count, 0) DESC, RANDOM() LIMIT 1'

FAKE_ROW = (
    7, 'segment_007.wav', 0.91, 'Тестова локація',
    date(2025, 6, 1), time(5, 30, 0), '/fake/path.wav',
    'Parus major', 'Синиця велика', 'Great Tit',
)


def _mock_conn(captured):
    """conn.execute(...) collects the SQL text and returns FAKE_ROW."""
    conn = MagicMock()

    def _execute(query, params=None):
        captured.append(str(query))
        res = MagicMock()
        res.fetchone.return_value = FAKE_ROW
        return res

    conn.execute.side_effect = _execute
    return conn


def test_next_segment_orders_by_verification_count(auth_client):
    """Branch without a species filter: ORDER BY verification_count DESC, RANDOM()."""
    cl = auth_client(role='admin')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        resp = cl.get('/uk/api/verification/next-segment')

    assert resp.status_code == 200
    assert EXPECTED_ORDER_BY in captured[-1]
    assert 'ORDER BY RANDOM() LIMIT 1' not in captured[-1].replace(
        EXPECTED_ORDER_BY, '')
    assert resp.get_json()['segment_id'] == 7


def test_next_segment_species_branch_orders_by_verification_count(auth_client):
    """Branch with a species filter: the same near-consensus priority."""
    cl = auth_client(role='admin')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        resp = cl.get('/uk/api/verification/next-segment?species_id=5')

    assert resp.status_code == 200
    assert EXPECTED_ORDER_BY in captured[-1]
    assert 'seg.species_id = :species_id' in captured[-1]


def test_next_segment_keeps_pending_and_own_votes_filters(auth_client):
    """The status='pending' filter and exclusion of own votes are still present."""
    cl = auth_client(role='admin')
    for url in ('/uk/api/verification/next-segment',
                '/uk/api/verification/next-segment?species_id=5'):
        captured = []
        with patch('app.pam.routes.get_pam_db_connection',
                   return_value=_mock_conn(captured)):
            resp = cl.get(url)
        assert resp.status_code == 200
        sql = captured[-1]
        assert "seg.status = 'pending'" in sql
        assert 'sv.user_id = :user_id' in sql
