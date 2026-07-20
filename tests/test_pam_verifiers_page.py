"""
Verifiers leaderboard page (GET /<lang>/pam/verification/verifiers).

PAM analogue of the camera-traps ``contributors`` page, added 2026-07-20:

1. Full ranking of segment verifiers with rolling-window columns
   (today / week / month / year / total), anchored to ``verified_at``,
   ordered by total desc.
2. Optional species / status filters mirror the segments-page "top verifiers"
   block and add the matching WHERE conditions to the aggregate query.
3. Usernames come from the main app DB; a fallback label is used when the id
   is unknown there.
4. The page requires the ``pam_verifier`` role (same as the segments page).

Run:
    venv/Scripts/python -m pytest tests/test_pam_verifiers_page.py -v
"""
from unittest.mock import MagicMock, patch


# user_id, d_today, d_week, d_month, d_year, total
LEADERBOARD_ROWS = [
    (0, 0, 0, 0, 4297, 4297),      # system import — unknown in main DB
    (3, 0, 292, 292, 3910, 3910),
    (1, 23, 433, 433, 1165, 1165),
]
SPECIES_ROWS = [(1, 'Erithacus rubecula', 'Вільшанка', 'European Robin')]


def _mock_conn(captured):
    """Dispatch execute() by SQL shape: the species dropdown query reads FROM
    species; the aggregate reads FROM segment_verifications."""
    conn = MagicMock()

    def _execute(query, params=None):
        sql = str(query)
        captured.append((sql, params or {}))
        res = MagicMock()
        if 'FROM segment_verifications' in sql:
            res.fetchall.return_value = LEADERBOARD_ROWS
        else:
            res.fetchall.return_value = SPECIES_ROWS
        return res

    conn.execute.side_effect = _execute
    return conn


def _agg_sql(captured):
    return next(sql for sql, _ in captured if 'FROM segment_verifications' in sql)


def _agg_params(captured):
    return next(p for sql, p in captured if 'FROM segment_verifications' in sql)


def test_verifiers_page_renders_ranked_rows(auth_client):
    """Rows render in the order the query returns them, with the total column."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn([])):
        resp = cl.get('/uk/pam/verification/verifiers')

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Unknown-in-main-DB id falls back to a stable label.
    assert 'User #0' in html
    # Totals from the aggregate are present.
    assert '4297' in html and '3910' in html and '1165' in html


def test_verifiers_query_orders_by_total_with_rolling_windows(auth_client):
    """The aggregate groups per user, orders by total desc and exposes the four
    rolling windows via FILTER clauses."""
    cl = auth_client(role='pam_verifier')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        cl.get('/uk/pam/verification/verifiers')

    sql = _agg_sql(captured)
    assert 'GROUP BY sv.user_id' in sql
    assert 'ORDER BY total DESC' in sql
    for key in (':start_today', ':start_week', ':start_month', ':start_year'):
        assert key in sql
    params = _agg_params(captured)
    assert {'start_today', 'start_week', 'start_month', 'start_year'} <= set(params)


def test_verifiers_species_and_status_filters_add_conditions(auth_client):
    """Passing species_id/status narrows the aggregate with bound params."""
    cl = auth_client(role='pam_verifier')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        resp = cl.get('/uk/pam/verification/verifiers?species_id=1&status=completed')

    assert resp.status_code == 200
    sql = _agg_sql(captured)
    params = _agg_params(captured)
    assert 'seg.species_id = :species_id' in sql
    assert 'seg.status = :status' in sql
    assert params['species_id'] == 1
    assert params['status'] == 'completed'


def test_verifiers_no_filter_has_no_where_clause(auth_client):
    """Without filters the aggregate must not append species/status predicates."""
    cl = auth_client(role='pam_verifier')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        cl.get('/uk/pam/verification/verifiers')

    sql = _agg_sql(captured)
    assert 'seg.species_id = :species_id' not in sql
    assert 'seg.status = :status' not in sql


def test_verifiers_page_requires_pam_verifier_role(auth_client):
    """A user without the pam_verifier role is forbidden (same gate as segments)."""
    cl = auth_client(role='volunteer_user')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn([])):
        resp = cl.get('/uk/pam/verification/verifiers')

    assert resp.status_code == 403


def test_segments_page_links_to_full_leaderboard(auth_client):
    """The segments page exposes a link to the full verifiers leaderboard."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn([])):
        resp = cl.get('/uk/pam/verification/segments')

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '/uk/pam/verification/verifiers' in html
