"""
#34: the CT identification nav arrows must have a transparent background at rest
(so they don't obscure an animal near the photo edge), with a dark background only on :hover.

Regression guard on the CSS contract (verified visually in production).
"""
import re
import pathlib

CSS = (pathlib.Path(__file__).resolve().parents[1]
       / 'app' / 'camera_traps' / 'static' / 'css' / 'camera_traps.css').read_text(encoding='utf-8')


def _base_nav_arrow_body():
    # Base rule `.nav-arrow { ... }` (NOT :hover, not .photo-viewer-main):
    # find the block that contains position: absolute.
    for m in re.finditer(r'\.nav-arrow\s*\{([^}]*)\}', CSS):
        if 'position: absolute' in m.group(1):
            return m.group(1)
    return None


def test_nav_arrow_transparent_at_rest():
    body = _base_nav_arrow_body()
    assert body is not None, 'базове правило .nav-arrow не знайдено'
    assert 'background-color: transparent' in body
    assert 'text-shadow' in body          # the glyph must stay readable


def test_nav_arrow_hover_has_visible_background():
    m = re.search(r'\.nav-arrow:hover\s*\{([^}]*)\}', CSS)
    assert m is not None, 'правило .nav-arrow:hover не знайдено'
    assert 'rgba(0, 0, 0' in m.group(1)   # visible dark background on hover
