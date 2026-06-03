"""
Task #16: spectrogram smooth playback line + click-to-seek + drag.

Це клієнтська (JS+CSS) фіча — pytest не виконує JS, тож тут лише
regression-guard: verify-сторінка рендериться і містить ключові
конструкції нової логіки. Поведінкова перевірка — вручну в браузері
(7 кроків у звіті).

Запуск:
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
    """Сторінка рендериться (200) і несе нову інтерактивну логіку."""
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
    """Константи осей matplotlib не змінені (12.0 / 1.6)."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_pam_conn()):
        html = cl.get('/uk/pam/verification/verify').get_data(as_text=True)
    assert 'SPECTROGRAM_PADDING_LEFT_PERCENT = 12.0' in html
    assert 'SPECTROGRAM_PADDING_RIGHT_PERCENT = 1.6' in html


def test_region_line_logic_preserved(auth_client):
    """Жовті region-lines для довгих файлів не зачеплено."""
    cl = auth_client(role='pam_verifier')
    with patch('app.pam.routes.get_pam_db_connection', return_value=_mock_pam_conn()):
        html = cl.get('/uk/pam/verification/verify').get_data(as_text=True)
    assert 'region-start-line' in html
    assert 'region-end-line' in html
    assert 'duration > 4' in html  # умова центральних 3с


def test_css_playback_line_is_draggable():
    """CSS: лінія тепер інтерактивна (pointer-events:auto, ew-resize),
    із розширеною hit-area через ::before."""
    css = CSS.read_text(encoding='utf-8')
    # знаходимо блок #playback-line
    start = css.index('#playback-line {')
    block = css[start:css.index('}', start) + 1]
    assert 'pointer-events: auto' in block
    assert 'cursor: ew-resize' in block
    assert 'touch-action: none' in block
    assert '#playback-line::before' in css
    # старе небезпечне правило прибрано саме з основного блоку
    assert 'pointer-events: none' not in block
