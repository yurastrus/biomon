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

from app.pam.verification_priorities import PRIORITY_CONFIDENCE, PRIORITY_ORDER

EXPECTED_ORDER_BY = 'ORDER BY COALESCE(seg.verification_count, 0) DESC, RANDOM() LIMIT 1'

# Mirrors the SELECT list of api_next_verification_segment, in order. The route
# reads positionally (result[0]…result[12]), so a short row raises IndexError →
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
    'Установа-власник',     # 12 rights_holder (institutions of the location)
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


def test_preferred_segment_only_changes_order_within_authorized_queue(auth_client):
    cl = auth_client(role='pam_verifier')
    conn = _mock_conn([])
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        response = cl.get('/uk/api/verification/next-segment?species_id=5&preferred_segment_id=7')

    assert response.status_code == 200
    query, params = conn.execute.call_args.args
    sql = str(query)
    assert 'ORDER BY CASE WHEN seg.id = :preferred_segment THEN 0 ELSE 1 END' in sql
    assert "seg.status = 'pending'" in sql
    assert 'sv.user_id = :user_id' in sql
    assert 's.required_role = ANY(:roles)' in sql
    assert 'l_pub.visibility_level = 0' in sql
    assert 'seg.species_id = :species_id' in sql
    assert params['preferred_segment'] == 7


def test_benefit_queue_ranks_scoped_candidates_against_visible_history(auth_client):
    cl = auth_client(role='pam_verifier')
    conn = _mock_conn([])
    url = (
        '/en/api/verification/next-segment?sort_by=benefit&species_id=5'
        '&class_name=Aves&institution_ids=3&location_ids=11&from_ts=2026-01-01'
        '&preferred_segment_id=7&skip_segment_id=8'
    )
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        response = cl.post(url, json={'segment_ids': [7, 8]})

    assert response.status_code == 200
    query, params = conn.execute.call_args.args
    sql = str(query)
    candidates, history = sql.split('), eligible_species AS')
    assert "seg.status = 'pending'" in candidates
    assert 'sv.user_id = :user_id' in candidates
    assert 'seg.species_id = :species_id' in candidates
    assert 's.class = :class_name' in candidates
    assert 'r_det.location_id = ANY(:det_location_ids)' in candidates
    assert '>= CAST(:from_ts AS timestamptz)' in candidates
    assert 'seg.id = ANY(CAST(:sample_ids AS bigint[]))' in candidates
    assert 'NOT (seg.id = ANY(CAST(:skipped_ids AS bigint[])))' in candidates
    assert 'li.institution_id = ANY(:institution_ids)' in candidates
    assert 'li.institution_id = ANY(:institution_ids)' in history
    assert sql.count('l_pub.visibility_level = 0') == 2
    assert sql.count('s.required_role = ANY(:roles)') == 2
    assert ':sample_ids' not in history and ':from_ts' not in history
    assert f'ORDER BY segment_id <> :preferred_segment, {PRIORITY_ORDER}' in history
    assert params['sample_ids'] == [7, 8]
    assert params['skipped_ids'] == [8]
    assert params['confidence'] == PRIORITY_CONFIDENCE


def test_benefit_queue_rejects_out_of_range_skip_id(auth_client):
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection') as connect:
        response = cl.get('/en/api/verification/next-segment?sort_by=benefit&skip_segment_id=0')

    assert response.status_code == 400
    connect.assert_not_called()


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
