"""
#34: навігаційні стрілки CT-ідентифікації мають прозорий фон у спокої
(щоб не затуляти тварину біля краю фото), а темний фон — лише на :hover.

Regression-guard на CSS-контракт (візуально перевіряється на проді).
"""
import re
import pathlib

CSS = (pathlib.Path(__file__).resolve().parents[1]
       / 'app' / 'camera_traps' / 'static' / 'css' / 'camera_traps.css').read_text(encoding='utf-8')


def _base_nav_arrow_body():
    # Базове правило `.nav-arrow { ... }` (НЕ :hover, не .photo-viewer-main):
    # знаходимо блок, що містить position: absolute.
    for m in re.finditer(r'\.nav-arrow\s*\{([^}]*)\}', CSS):
        if 'position: absolute' in m.group(1):
            return m.group(1)
    return None


def test_nav_arrow_transparent_at_rest():
    body = _base_nav_arrow_body()
    assert body is not None, 'базове правило .nav-arrow не знайдено'
    assert 'background-color: transparent' in body
    assert 'text-shadow' in body          # символ має лишатись читабельним


def test_nav_arrow_hover_has_visible_background():
    m = re.search(r'\.nav-arrow:hover\s*\{([^}]*)\}', CSS)
    assert m is not None, 'правило .nav-arrow:hover не знайдено'
    assert 'rgba(0, 0, 0' in m.group(1)   # видимий темний фон при наведенні
