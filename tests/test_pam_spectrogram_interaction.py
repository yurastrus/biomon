"""
Task #16: spectrogram smooth playback line + click-to-seek + drag.

This is a client-side (JS+CSS) feature -- pytest does not run JS, so this is
only a regression guard: the verify page renders and contains the key
constructs of the new logic. Behavioral testing is done manually in a browser
(7 steps in the report).

Run:
    venv/Scripts/python -m pytest tests/test_pam_spectrogram_interaction.py -v
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / 'app/pam/templates/pam_verification_interface.html'
CSS = REPO_ROOT / 'app/pam/static/css/pam_style.css'


def _mock_pam_conn():
    conn = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    result.scalar.return_value = 0
    result.__iter__ = lambda self: iter([])
    conn.execute.return_value = result
    return conn


def test_verify_page_renders_with_interaction_js(auth_client):
    """Page renders (200) and carries the new interactive logic."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_pam_conn()):
        resp = cl.get('/uk/pam/verification/verify')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # RAF smooth-loop
    assert 'requestAnimationFrame' in html
    assert 'cancelAnimationFrame' in html
    # click-to-seek + drag helpers
    assert 'clientXToTime' in html
    assert 'isDragging' in html
    assert "addEventListener('mousedown'" in html
    assert "addEventListener('touchmove'" in html
    # a11y
    assert 'role="slider"' in html


def test_padding_constants_unchanged(auth_client):
    """Matplotlib axis constants unchanged (12.0 / 1.6)."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_pam_conn()):
        html = cl.get('/uk/pam/verification/verify').get_data(as_text=True)
    assert 'SPECTROGRAM_PADDING_LEFT_PERCENT = 12.0' in html
    assert 'SPECTROGRAM_PADDING_RIGHT_PERCENT = 1.6' in html


def test_region_line_logic_preserved(auth_client):
    """Yellow region-lines for long files are left untouched."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_pam_conn()):
        html = cl.get('/uk/pam/verification/verify').get_data(as_text=True)
    assert 'region-start-line' in html
    assert 'region-end-line' in html
    assert 'duration > 4' in html  # condition for the central 3s


def test_verification_grid_right_column_is_flexible():
    """#33: the right column (spectrogram) must be minmax(0, 760px), not a
    hard 760px -- otherwise on narrow screens the right edge gets clipped."""
    css = CSS.read_text(encoding='utf-8')
    start = css.index('.verification-layout {')
    block = css[start:css.index('}', start) + 1]
    assert 'minmax(0, 760px)' in block, (
        'права колонка має бути гнучкою (minmax), щоб сонограма вписувалась')
    assert '480px 760px' not in block, 'жорсткий grid повертати не можна'


def test_css_playback_line_is_draggable():
    """CSS: the line is now interactive (pointer-events:auto, ew-resize),
    with an expanded hit-area via ::before."""
    css = CSS.read_text(encoding='utf-8')
    # find the #playback-line block
    start = css.index('#playback-line {')
    block = css[start:css.index('}', start) + 1]
    assert 'pointer-events: auto' in block
    assert 'cursor: ew-resize' in block
    assert 'touch-action: none' in block
    assert '#playback-line::before' in css
    # the old unsafe rule is removed specifically from the main block
    assert 'pointer-events: none' not in block
