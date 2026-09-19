"""
xeno-canto cross-check link on the PAM verify page
(GET /<lang>/pam/verification/verify, GET /<lang>/api/verification/next-segment).

A verifier judging a BirdNET segment often needs a reference recording of the
same species. Before this, that meant leaving the queue and searching by hand.
The page now carries a link filtered to the current segment's species, which
needs the Latin binomial as its own field in the segment payload (the display
name is "Common name (Scientific name)" and is not safely parseable).

Covered:
  1. next-segment returns scientific_name alongside species_display_name
  2. the page ships the link element and the helper that fills it
  3. no scientific name -> the link stays hidden rather than searching for ""

Run:
    venv/Scripts/python -m pytest tests/test_pam_verify_xenocanto_link.py -v
"""
from datetime import date, datetime
from unittest.mock import MagicMock, patch

VERIFY_URL = '/uk/pam/verification/verify'
NEXT_URL = '/uk/api/verification/next-segment'

# Column order as read by api_get_next_segment (result[0]..result[12]).
SEGMENT_ROW = (
    42,                              # 0 segment_id
    'PARMAJ_20260601_0530.flac',     # 1 filename
    0.8765,                          # 2 confidence
    'Rozt_01',                       # 3 filename-parsed location
    date(2026, 6, 1),                # 4 recorded date
    datetime(2026, 6, 1, 5, 30),     # 5 recorded time
    None,                            # 6 (unused here)
    'Parus major',                   # 7 scientific_name
    'Синиця велика',                 # 8 common_name_uk
    'Great Tit',                     # 9 common_name_en
    'Розточчя',                      # 10 location_name_uk
    'Roztochia',                     # 11 location_name_en
    'Природний заповідник «Розточчя»',  # 12 rights_holder
)


def _mock_conn(row=SEGMENT_ROW):
    conn = MagicMock()
    res = MagicMock()
    res.fetchone.return_value = row
    res.fetchall.return_value = []
    res.mappings.return_value.fetchall.return_value = []
    conn.execute.return_value = res
    return conn


# ── 1. payload ──────────────────────────────────────────────────────────────

def test_next_segment_exposes_scientific_name(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_conn()):
        resp = cl.get(NEXT_URL)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['scientific_name'] == 'Parus major'
    # The display name keeps its existing shape — this is an addition, not a swap.
    assert 'Parus major' in data['species_display_name']


# ── 2. page wiring ──────────────────────────────────────────────────────────

def test_verify_page_has_xenocanto_link_element(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_conn()):
        resp = cl.get(VERIFY_URL)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="segment-xc-link"' in html
    assert 'xeno-canto.org/explore?query=' in html
    # Opens away from the queue, without handing the referrer to a third party.
    assert 'rel="noopener noreferrer"' in html


def test_verify_page_fills_link_from_scientific_name(auth_client):
    """displaySegment must feed the payload field into the link helper."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_conn()):
        html = cl.get(VERIFY_URL).get_data(as_text=True)

    assert 'updateXenoCantoLink(segment.scientific_name)' in html
    assert 'encodeURIComponent(scientificName)' in html


# ── 3. degradation ──────────────────────────────────────────────────────────

def test_link_hidden_when_species_has_no_scientific_name(auth_client):
    """The helper hides the link on a falsy name, so no empty xeno-canto search."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_conn()):
        html = cl.get(VERIFY_URL).get_data(as_text=True)

    assert 'if (!scientificName)' in html
    assert 'link.hide();' in html
    # Hidden until a segment is actually loaded.
    assert 'id="segment-xc-link"' in html
    assert 'style="display:none;"' in html
