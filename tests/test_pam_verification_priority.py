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

# Mirrors the SELECT list of api_next_verification_segment, in order. The route
# reads positionally (result[0]…result[11]), so a short row raises IndexError →
# 500. Keep this tuple in step with the query; the arity assertion below is the
# guard that makes a drift fail loudly instead of as a mystery 500.
FAKE_ROW = (
    7,                      # 0  seg.id
    'segment_007.wav',      # 1  seg.filename
    0.91,                   # 2  seg.confidence_level
    'Тестова локація',      # 3  seg.location_name (parsed from filename)
    date(2025, 6, 1),       # 4  seg.recorded_date
    time(5, 30, 0),         # 5  seg.recorded_time
    '/fake/path.wav',       # 6  seg.file_path
    'Parus major',          # 7  s.scientific_name
    'Синиця велика',        # 8  s.common_name_uk
    'Great Tit',            # 9  s.common_name_en
    'Локація з реєстру',    # 10 l.location_name      (loc_name_uk)
    'Registry location',    # 11 l.location_name_en   (loc_name_en)
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


def test_fake_row_arity_matches_route_positional_reads():
    """Guard for the failure this file used to have: the route reads the row
    positionally, so when the SELECT list grew (bilingual location from the
    locations registry) and FAKE_ROW did not, every test here turned into an
    IndexError -> 500 with no hint about the cause.

    Reads app/pam/routes.py — a shared public submodule — read-only.
    """
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1]
           / 'app' / 'pam' / 'routes.py').read_text(encoding='utf-8')
    start = src.index('def api_next_verification_segment')
    body = src[start:src.index('\n@pam_bp.route', start)]

    indices = {int(m) for m in re.findall(r'\bresult\[(\d+)\]', body)}
    assert indices, 'expected positional result[N] reads in the route'
    assert max(indices) < len(FAKE_ROW), (
        f'the route reads result[{max(indices)}] but FAKE_ROW has only '
        f'{len(FAKE_ROW)} columns — keep it in step with the SELECT list'
    )


def test_next_segment_prefers_registry_location_over_filename(auth_client):
    """The two columns that were missing are actually exercised: the location
    shown comes from the locations registry, not from the parsed filename."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn([])):
        resp = cl.get('/uk/api/verification/next-segment')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['location_name'] == 'Локація з реєстру'  # FAKE_ROW[10]
    assert body['location_name'] != 'Тестова локація'    # FAKE_ROW[3], fallback
