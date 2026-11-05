"""/upload-fast map (2026-11-05): stacked pins fan out, count filter is one
two-thumb slider whose left thumb is "at least" and right thumb "at most".

Template contract only; the behaviour was checked in the browser with
scripts/upload_fast_map_ui_harness.py.
"""
import pathlib
import re

CT = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'camera_traps'
TPL = (CT / 'templates' / 'upload_fast.html').read_text(encoding='utf-8')
JS = (CT / 'static' / 'js' / 'marker_spread.js').read_text(encoding='utf-8')


def test_page_loads_marker_spread_and_routes_clicks_through_it():
    assert "filename='js/marker_spread.js'" in TPL
    assert 'CTMarkerSpread.attach(map' in TPL
    # A pin click must ask the spreader first, else stacked pins select the top one.
    assert re.search(r"spread\.consumeClick\(marker\)\)\s*selectLocation", TPL)
    # Filters hide/show pins: the fan must be rebuilt after that.
    assert 'spread.refresh()' in TPL


def test_marker_spread_exposes_auto_and_click_modes():
    assert 'global.CTMarkerSpread' in JS
    assert 'autoZoom' in JS and 'consumeClick' in JS
    # True coordinates are kept; only the drawn position moves.
    assert '_ctHome' in JS


def test_count_filter_is_one_two_thumb_slider():
    m = re.search(r'id="filter-count-range".*?id="filter-count-label"', TPL, re.S)
    assert m, 'count slider container missing'
    block = m.group(0)
    assert block.index('filter-count-min') < block.index('filter-count-max')
    assert 'dual-range' in block


def test_count_thumbs_cannot_cross():
    # The old code swapped min/max when they crossed, which made the thumbs
    # look reversed. Now the moved thumb is clamped to the other one.
    assert 'clampCountThumbs' in TPL
    assert '[cMin, cMax] = [cMax, cMin]' not in TPL
    assert '[lo, hi] = [hi, lo]' not in TPL
