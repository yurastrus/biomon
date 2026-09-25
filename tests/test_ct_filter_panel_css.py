"""
Filter panels on contributors / expertise / upload stats must not stretch
vertically on narrow screens.

The panel becomes a flex column under 992px, so a row flex-basis (320px)
turns into a HEIGHT and pads each group with empty space. The sizing therefore
lives in classes that the mobile query resets, never in inline styles (which
the query cannot override).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'camera_traps'
CSS = (ROOT / 'static' / 'css' / 'camera_traps.css').read_text(encoding='utf-8')
TEMPLATES = ['contributors.html', 'expertise.html', 'upload_stats.html']


def _mobile_block():
    start = CSS.index('@media (max-width: 992px)')
    return CSS[start:start + 3000]


def test_templates_have_no_inline_flex_basis_on_filter_groups():
    for name in TEMPLATES:
        html = (ROOT / 'templates' / name).read_text(encoding='utf-8')
        assert not re.search(r'class="filter-group[^"]*"\s+style="flex:', html), name
        assert 'filter-group-wide' in html, name


def test_desktop_sizing_classes_defined():
    assert re.search(r'\.filter-group-wide\s*\{[^}]*flex:\s*0 1 320px', CSS)
    assert re.search(r'\.filter-group-narrow\s*\{[^}]*flex:\s*0 1 130px', CSS)


def test_mobile_query_resets_flex_basis():
    block = _mobile_block()
    m = re.search(r'\.filter-group-wide,\s*\.filters-panel \.filter-group-narrow\s*\{([^}]*)\}', block)
    assert m, 'mobile reset for filter-group-wide/narrow not found'
    assert 'flex: 0 0 auto' in m.group(1)
