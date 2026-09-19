"""Copyright notice under the spectrogram on the PAM verification page.

Recordings belong to the institution that runs the recorder, not to the platform
and not to the verifier listening to them. The verify page now names that owner
directly under the spectrogram, resolved from the location's institutions in the
PAM database (`location_institutions` → `institutions`) rather than hard-coded.

Covered:
  1. next-segment carries `rights_holder`, and the SQL resolves it per language
  2. a location with no institution yields None (page falls back to the generic
     notice instead of printing an empty line)
  3. the page ships the element and the text

Run:
    venv/Scripts/python -m pytest tests/test_pam_verification_rights.py -v
"""
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

NEXT_URL = '/uk/api/verification/next-segment'
TEMPLATE = (Path(__file__).resolve().parents[1]
            / 'app/pam/templates/pam_verification_interface.html')

ROW = (
    11, 'PARMAJ_20260601_0530.flac', 0.9, 'Rozt_01',
    date(2026, 6, 1), datetime(2026, 6, 1, 5, 30), None,
    'Parus major', 'Синиця велика', 'Great Tit',
    'Розточчя', 'Roztochia',
    'Природний заповідник «Розточчя»',   # rights_holder
)


def _conn(row, captured=None):
    conn = MagicMock()
    res = MagicMock()
    res.fetchone.return_value = row
    res.fetchall.return_value = []
    res.mappings.return_value.fetchall.return_value = []

    def _execute(sql, params=None):
        if captured is not None:
            captured['sql'] = str(sql)
            captured['params'] = params or {}
        return res

    conn.execute.side_effect = _execute
    return conn


def test_next_segment_names_the_owning_institution(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_conn(ROW)):
        resp = cl.get(NEXT_URL)

    assert resp.status_code == 200
    assert resp.get_json()['rights_holder'] == 'Природний заповідник «Розточчя»'


def test_rights_holder_is_none_when_location_has_no_institution(auth_client):
    """No owner recorded -> None, so the page shows the generic notice."""
    cl = auth_client(role='admin')
    row = ROW[:12] + (None,)
    with patch('app.pam.routes.get_pam_db_connection', return_value=_conn(row)):
        resp = cl.get(NEXT_URL)

    assert resp.status_code == 200
    assert resp.get_json()['rights_holder'] is None


def test_owner_query_follows_the_interface_language(auth_client):
    """The English page must get name_en, not the Ukrainian name."""
    cl = auth_client(role='admin')
    captured = {}
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_conn(ROW, captured)):
        resp = cl.get('/en/api/verification/next-segment')

    assert resp.status_code == 200
    assert 'location_institutions' in captured['sql']
    assert captured['params'].get('rights_lang') == 'en'


def test_verify_page_renders_the_notice_under_the_spectrogram():
    text = TEMPLATE.read_text(encoding='utf-8')
    assert 'segment-copyright' in text
    assert 'Копіювання і поширення заборонене' in text
    # Placement is the point: the notice belongs to the spectrogram block, not
    # to the audio-player block below it.
    spectrogram = text.index('spectrogram-section')
    notice = text.index('id="segment-copyright"')
    player = text.index('audio-player-section')
    assert spectrogram < notice < player
