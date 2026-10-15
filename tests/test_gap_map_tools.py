"""Map tools of the gap analysis pages (planned points, KML/GPX, ruler, PAM buffer).

The JavaScript (static/js/gap_map_tools.js) has a copy in each submodule; the
copies are independent, so each is checked on its own. No JS runtime is
available in CI, so these are static checks of the file and of the pages that
load it.

Run:
    venv/Scripts/python -m pytest tests/test_gap_map_tools.py -v
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.pam import gap_routes as pam_gap_routes

JS_COPIES = {
    'ct': Path('app/camera_traps/static/js/gap_map_tools.js'),
    'pam': Path('app/pam/static/js/gap_map_tools.js'),
}


@pytest.mark.parametrize('module', sorted(JS_COPIES))
def test_js_has_export_ruler_and_buffer(module):
    js = JS_COPIES[module].read_text(encoding='utf-8')
    assert 'global.GapMapTools = {attach' in js
    assert 'xmlns="http://www.opengis.net/kml/2.2"' in js      # KML 2.2
    assert 'xmlns="http://www.topografix.com/GPX/1/1"' in js   # GPX 1.1
    assert '${p.lon.toFixed(6)},${p.lat.toFixed(6)},0' in js     # KML is lon,lat
    assert 'map.distance(' in js                                # ruler
    assert "dashArray: '6 5'" in js                              # dashed buffer ring
    assert 'localStorage' in js and 'catch (e)' in js            # guarded storage


@pytest.fixture
def pam_page_db():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    with patch.object(pam_gap_routes, 'get_pam_db_connection', return_value=conn), \
         patch.object(pam_gap_routes, 'get_models_list', return_value=[]):
        yield


def _tool_ids(html):
    return all(f'id="{i}"' in html for i in (
        'tool-add-point', 'tool-ruler', 'tool-ruler-clear', 'planned-list',
        'planned-kml', 'planned-gpx', 'planned-clear', 'tool-hint', 'planned-count'))


def test_ct_page_has_tools_without_buffer(auth_client):
    html = auth_client(role='viewer').get('/uk/camera-traps/analysis/gap-analysis').get_data(as_text=True)
    assert _tool_ids(html)
    assert 'js/gap_map_tools.js' in html
    assert 'buffer-range' not in html
    assert "storageKey: 'biomon-ct-gap-planned'" in html


def test_pam_page_has_tools_and_buffer_slider(auth_client, pam_page_db):
    html = auth_client(role='viewer').get('/uk/pam/gap-analysis').get_data(as_text=True)
    assert _tool_ids(html)
    assert 'js/gap_map_tools.js' in html
    assert 'id="buffer-range" min="50" max="2000" step="10" value="500"' in html
    assert "storageKey: 'biomon-pam-gap-planned'" in html


def test_buffer_limits():
    assert (pam_gap_routes.GAP_BUFFER_MIN_M, pam_gap_routes.GAP_BUFFER_MAX_M,
            pam_gap_routes.GAP_BUFFER_DEFAULT_M) == (50, 2000, 500)


@pytest.mark.parametrize('url', [
    '/uk/camera-traps/ct-static/js/gap_map_tools.js',
    '/uk/pam-static/js/gap_map_tools.js',
])
def test_js_is_served(client, url):
    resp = client.get(url)
    assert resp.status_code == 200 and b'GapMapTools' in resp.data
