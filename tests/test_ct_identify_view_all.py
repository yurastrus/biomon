"""
/identify: an answer only after every frame of the series was on screen.

Asked 02.11.2026: a series with several photos must not be submitted before
all of them were looked at (arrows, Home/End, autoplay). Guards against
answering by the first frame, including when autoplay was left paused.
The button stays inactive with a hover title explaining why; Ctrl+Enter shows
the same text for 2 seconds. Correcting one's own answer ("<") is not gated.

The behaviour is in the page's JS; these checks pin the pieces in the template.
The UI itself was checked in scripts/identify_hotkeys_ui_harness.py.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identify_view_all.py -v
"""
import re
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parents[1]
            / 'app' / 'camera_traps' / 'templates' / 'identification.html')
CSS = (Path(__file__).resolve().parents[1]
       / 'app' / 'camera_traps' / 'static' / 'css' / 'camera_traps.css')


def _html():
    return TEMPLATE.read_text(encoding='utf-8')


def _function_body(html, name):
    start = html.index(f'function {name}(')
    return html[start:html.index('\n    }\n', start)]


def test_every_displayed_frame_counts_as_seen():
    body = _function_body(_html(), 'displayCurrentPhoto')
    assert 'seenPhotos.add(currentPhotoIndex)' in body
    assert 'updateSubmitGate()' in body


def test_seen_frames_reset_for_each_new_series():
    html = _html()
    reset = html.index('seenPhotos = new Set();\n                resetPrefetch();')
    assert reset < html.index('displayCurrentPhoto();', reset)


def test_correcting_own_answer_is_not_gated():
    assert 'editingSubmitted) return 0' in _function_body(_html(), 'unseenPhotoCount')


def test_button_is_gated_by_class_not_disabled_attribute():
    body = _function_body(_html(), 'updateSubmitGate')
    assert "toggleClass('submit-gated'" in body
    assert "attr('title'" in body
    assert "prop('disabled'" not in body
    assert '.submit-gated' in CSS.read_text(encoding='utf-8')


def test_submit_handler_refuses_while_frames_are_unseen():
    html = _html()
    handler = html[html.index("form.on('submit'"):]
    first_lines = handler[:300]
    assert 'if (unseenPhotoCount() > 0) return;' in first_lines


def test_ctrl_enter_warns_for_two_seconds_instead_of_submitting():
    html = _html()
    ctrl = html[html.index('// Ctrl+Enter submits from anywhere'):][:600]
    assert re.search(r'if \(unseenPhotoCount\(\) > 0\) warnUnseenPhotos\(\);\s*else submitBtn', ctrl)
    warn = _function_body(html, 'warnUnseenPhotos')
    assert '2000' in warn
    assert 'id="unseen-warning"' in html
