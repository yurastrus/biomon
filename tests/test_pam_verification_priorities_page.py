"""
Verification priorities page (GET /<lang>/pam/verification/priorities).

Covers the two behaviours added on 2026-07-17:

1. The species table lists every species that has segments OR detections
   (detection-only species must appear too), and each row is coloured on a
   continuous scale from zero verified segments to the verification threshold.

2. For a ``pam_verifier`` the table gains an action column with a deep link
   into the verify interface (``?species_id=<id>``) for every species that
   still has segments this user can personally verify.

Run:
    venv/Scripts/python -m pytest tests/test_pam_verification_priorities_page.py -v
"""
import re
from html import unescape
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _row(species_id, sci, total, verified, consensus, detections, redbook=None):
    return SimpleNamespace(
        species_id=species_id,
        scientific_name=sci,
        common_name_uk=f'{sci}-uk',
        common_name_en=f'{sci}-en',
        class_name='Aves', order_name='Passeriformes',
        family_name='Paridae', genus_name='Parus',
        total_segments=total, verified_segments=verified,
        consensus_segments=consensus, detection_count=detections,
        redbook2021=redbook,
    )


# Species A: detections only (no segments)  -> red
# Species B: segments, under threshold       -> orange, verifiable
# Species C: enough verified                 -> green
MAIN_ROWS = [
    _row(1, 'Detectus only', 0, 0, 0, 120),
    _row(2, 'Segmentus few', 49, 2, 0, 150),
    _row(3, 'Verifius done', 300, 300, 280, 400),
    _row(4, 'Segmentus fifty', 50, 2, 0, 150),
    _row(5, 'Segmentus legacy', 60, 20, 10, 0),
]
PENDING_ROWS = [
    SimpleNamespace(species_id=2, remaining=5),
    SimpleNamespace(species_id=4, remaining=5),
]


def _mock_conn(captured, cuttable_ids=()):
    """Dispatch execute() by SQL shape: the per-user pending query groups by
    seg.species_id; everything else is the main species table query."""
    conn = MagicMock()

    def _execute(query, params=None):
        sql = str(query)
        captured.append(sql)
        res = MagicMock()
        if 'GROUP BY seg.species_id' in sql:
            res.fetchall.return_value = PENDING_ROWS
        elif 'AND li.institution_id = ANY(:managed_ids)' in sql:
            res.scalars.return_value.all.return_value = cuttable_ids
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


def test_priorities_has_order_index_column(auth_client):
    """The original priority order is exposed as a sortable # column so users
    can restore it after re-sorting by another column."""
    cl = auth_client(role='manager')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_mock_conn([])):
        resp = cl.get('/uk/pam/verification/priorities')

    html = resp.get_data(as_text=True)
    assert 'order-cell' in html
    # One numbered cell per species row (1..N in render order).
    assert 'data-sort="1"' in html and 'data-sort="3"' in html


def test_priorities_colour_gradient(auth_client):
    """Hue follows verified counts, including zero and the upper cap."""
    cl = auth_client(role='manager')
    conn = _mock_conn([])
    rows = [
        _row(1, 'No segments', 0, 0, 0, 100),
        _row(2, 'No votes', 10, 0, 0, 100),
        _row(3, 'One vote', 20, 1, 0, 100),
        _row(4, 'Halfway', 140, 125, 5, 100),
        _row(5, 'At target', 260, 250, 200, 300),
        _row(6, 'Above target', 300, 280, 200, 300),
    ]
    conn.execute.side_effect = None
    conn.execute.return_value.fetchall.side_effect = [rows, []]
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/uk/pam/verification/priorities')

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    hues = {
        name: float(hue) for hue, name in re.findall(
            r'<tr style="--priority-hue: ([\d.]+)">(?:(?!</tr>).)*?<em>([^<]+)</em>',
            html, re.S,
        )
    }
    assert hues == {
        'No segments': 0, 'No votes': 0, 'One vote': 0.53,
        'Halfway': 66, 'At target': 132, 'Above target': 132,
    }
    assert 'legend-gradient' in html
    assert '<span>250+</span>' in html


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
    """Sort by verified counts, then by Red Book and detections among zeros."""
    shuffled = [
        _row(10, 'Many verified', 300, 300, 280, 400, 'rare'),
        _row(11, 'One verified', 20, 1, 1, 50),
        _row(12, 'Common abundant', 0, 0, 0, 900),
        _row(13, 'Listed scarce', 5, 0, 0, 30, 'rare'),
        _row(14, 'Listed abundant', 0, 0, 0, 90, 'vulnerable'),
        _row(15, 'Common scarce', 100, 0, 0, 10),
        _row(16, 'Two verified', 300, 2, 0, 999),
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

    assert resp.status_code == 200
    assert re.findall(r'<em>([^<]+)</em>', resp.get_data(as_text=True)) == [
        'Listed abundant', 'Listed scarce', 'Common abundant', 'Common scarce',
        'One verified', 'Two verified', 'Many verified',
    ]


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
    # Deep links only for species with pending segments.
    assert 'verify-link' in html
    assert 'species_id=2' in html
    assert 'species_id=1' not in html
    assert 'species_id=3' not in html
    assert 'species_id=4' in html


@pytest.mark.parametrize('role,manages,cuttable_ids,expected_species', [
    ('admin', False, (), {'Detectus only', 'Segmentus few', 'Verifius done', 'Segmentus fifty'}),
    ('manager', True, (1, 3), {'Detectus only', 'Verifius done'}),
    ('manager', True, (), set()),
    ('manager', False, (1,), set()),
    ('pam_verifier', False, (1,), set()),
])
def test_priorities_cut_link_respects_scope(auth_client, role, manages, cuttable_ids, expected_species):
    cl = auth_client(role=role, manages=manages)
    conn = _mock_conn([], cuttable_ids)
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = cl.get('/en/pam/verification/priorities')

    assert resp.status_code == 200
    html = unescape(resp.get_data(as_text=True))
    links = re.findall(r'/en/pam/verification/sample-upload\?source=priorities&species=([^"]+)', html)
    assert {name.replace('+', ' ') for name in links} == expected_species
    assert ('Cut segments' in html) == bool(expected_species)
    scoped_queries = [
        call.args for call in conn.execute.call_args_list
        if 'AND li.institution_id = ANY(:managed_ids)' in str(call.args[0])
    ]
    assert bool(scoped_queries) == manages
    if manages:
        sql, params = scoped_queries[0]
        assert 's.required_role = ANY(:roles)' in str(sql)
        assert len(params['managed_ids']) == 1
        assert params['species_ids'] == [1, 2, 3, 4]


def test_priorities_show_both_actions_when_both_are_available(auth_client):
    cl = auth_client(role='manager', manages=True)
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_conn([], (2, 4))):
        resp = cl.get('/en/pam/verification/priorities')

    assert resp.status_code == 200
    html = unescape(resp.get_data(as_text=True))
    few_row = html.split('<em>Segmentus few</em>', 1)[1].split('</tr>', 1)[0]
    assert 'class="btn btn-sm btn-primary verify-link"' in few_row
    assert 'species_id=2' in few_row
    assert 'class="btn btn-sm btn-success"' in few_row
    assert 'source=priorities&species=Segmentus+few' in few_row
    fifty_row = html.split('<em>Segmentus fifty</em>', 1)[1].split('</tr>', 1)[0]
    assert 'species_id=4' in fifty_row
    assert 'source=priorities&species=Segmentus+fifty' in fifty_row
