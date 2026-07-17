"""
Verification priorities page (GET /<lang>/pam/verification/priorities).

Covers the two behaviours added on 2026-07-17:

1. The species table lists every species that has segments OR detections
   (detection-only species must appear too), and each row is colour-flagged:
     * priority-none  -> detections but no segments   (red)
     * priority-low   -> segments but under-verified  (orange)
     * priority-ok    -> enough verified              (green)

2. For a ``pam_verifier`` the table gains an action column with a deep link
   into the verify interface (``?species_id=<id>``) for every species that
   still has segments this user can personally verify.

Run:
    venv/Scripts/python -m pytest tests/test_pam_verification_priorities_page.py -v
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _row(species_id, sci, total, verified, consensus, detections):
    return SimpleNamespace(
        species_id=species_id,
        scientific_name=sci,
        common_name_uk=f'{sci}-uk',
        common_name_en=f'{sci}-en',
        class_name='Aves', order_name='Passeriformes',
        family_name='Paridae', genus_name='Parus',
        total_segments=total, verified_segments=verified,
        consensus_segments=consensus, detection_count=detections,
    )


# Species A: detections only (no segments)  -> red
# Species B: segments, under threshold       -> orange, verifiable
# Species C: enough verified                 -> green
MAIN_ROWS = [
    _row(1, 'Detectus only', 0, 0, 0, 120),
    _row(2, 'Segmentus few', 10, 2, 0, 50),
    _row(3, 'Verifius done', 300, 300, 280, 400),
]
PENDING_ROWS = [SimpleNamespace(species_id=2, remaining=5)]


def _mock_conn(captured):
    """Dispatch execute() by SQL shape: the per-user pending query groups by
    seg.species_id; everything else is the main species table query."""
    conn = MagicMock()

    def _execute(query, params=None):
        sql = str(query)
        captured.append(sql)
        res = MagicMock()
        if 'GROUP BY seg.species_id' in sql:
            res.fetchall.return_value = PENDING_ROWS
        else:
            res.fetchall.return_value = MAIN_ROWS
        return res

    conn.execute.side_effect = _execute
    return conn


def test_priorities_includes_detection_only_species(auth_client):
    """Main query must keep species that have detections but no segments."""
    cl = auth_client(role='manager')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        resp = cl.get('/uk/pam/verification/priorities')

    assert resp.status_code == 200
    main_sql = captured[0]
    assert 'seg.species_id IS NOT NULL OR d.species_id IS NOT NULL' in main_sql
    # LEFT JOIN on segments so detection-only species survive.
    assert 'LEFT JOIN' in main_sql


def test_priorities_colour_flags(auth_client):
    """Each row carries the right colour class for its state."""
    cl = auth_client(role='manager')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn([])):
        resp = cl.get('/uk/pam/verification/priorities')

    html = resp.get_data(as_text=True)
    assert 'priority-none' in html   # detection-only species
    assert 'priority-low' in html    # under-verified
    assert 'priority-ok' in html     # enough verified


def test_priorities_non_verifier_has_no_verify_column(auth_client):
    """A user who can't verify (volunteer_user has no pam_verifier via hierarchy)
    never triggers the per-user pending query nor gets the link."""
    cl = auth_client(role='volunteer_user')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        resp = cl.get('/uk/pam/verification/priorities')

    html = resp.get_data(as_text=True)
    assert 'verify-link' not in html
    assert not any('GROUP BY seg.species_id' in s for s in captured)


def test_priorities_default_sort_order(auth_client):
    """Default order: actionable (orange, has segments, under-verified) first,
    ascending segment count within status; then detection-only (red); then
    fully-verified (green) last."""
    # Intentionally shuffled input so only the server-side sort can fix it.
    shuffled = [
        _row(10, 'Green done', 300, 300, 280, 400),   # green   -> last
        _row(11, 'Orange many', 20, 1, 0, 50),        # orange, 20 segs
        _row(12, 'Red nosegs', 0, 0, 0, 90),          # red     -> middle
        _row(13, 'Orange few', 5, 0, 0, 30),          # orange, 5 segs -> first
    ]

    conn = MagicMock()

    def _execute(query, params=None):
        res = MagicMock()
        # manager also triggers the per-user pending query -> return no rows.
        res.fetchall.return_value = (
            [] if 'GROUP BY seg.species_id' in str(query) else shuffled)
        return res
    conn.execute.side_effect = _execute

    cl = auth_client(role='manager')
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/pam/verification/priorities')

    html = resp.get_data(as_text=True)
    order = [html.index(name) for name in
             ('Orange few', 'Orange many', 'Red nosegs', 'Green done')]
    assert order == sorted(order), f'unexpected row order: {order}'


def test_priorities_verifier_gets_deep_link(auth_client):
    """A pam_verifier gets a species-filtered verify link for verifiable rows."""
    cl = auth_client(role='pam_verifier')
    captured = []
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn(captured)):
        resp = cl.get('/uk/pam/verification/priorities')

    assert resp.status_code == 200
    # Per-user pending query ran with the queue predicate.
    pending_sql = [s for s in captured if 'GROUP BY seg.species_id' in s]
    assert pending_sql, 'expected a per-user pending-by-species query'
    assert "seg.status = 'pending'" in pending_sql[0]
    assert 'sv.user_id = :user_id' in pending_sql[0]

    html = resp.get_data(as_text=True)
    # Deep link only for species 2 (the one with remaining > 0).
    assert 'verify-link' in html
    assert 'species_id=2' in html
    assert 'species_id=1' not in html
    assert 'species_id=3' not in html
