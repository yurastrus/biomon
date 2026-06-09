"""
#45: усі карти фотопасток мають перемикач OSM↔Супутник, дефолт — OSM.

Regression-guard на контракт шаблонів (візуально перевіряється на проді).
"""
import re
import pathlib

import pytest

CT_TPL = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'camera_traps' / 'templates'
MAPS = [
    'dashboard.html',
    'service_log.html',
    'manage_locations.html',
    'data_quality.html',
    'manage_deployments.html',
    'upload.html',
    'upload_fast.html',
    'species_detailed.html',
]

# OSM як дефолт: або `osm/osmLayer.addTo(map|window.map)`, або OSM-тайл із chained .addTo(map)
_OSM_DEFAULT = re.compile(
    r'osm\w*\.addTo\((?:window\.)?map\)|openstreetmap[^;]*?\.addTo\(map\)',
    re.IGNORECASE,
)


@pytest.mark.parametrize('fname', MAPS)
def test_ct_map_has_osm_satellite_toggle(fname):
    src = (CT_TPL / fname).read_text(encoding='utf-8')
    assert 'World_Imagery' in src, f'{fname}: немає супутникового шару'
    assert 'L.control.layers' in src, f'{fname}: немає перемикача шарів'


@pytest.mark.parametrize('fname', MAPS)
def test_ct_map_defaults_to_osm(fname):
    src = (CT_TPL / fname).read_text(encoding='utf-8')
    assert _OSM_DEFAULT.search(src), f'{fname}: OSM не є дефолтним базовим шаром'
    # старий дефолт-«супутник» прибрано
    assert 'hybridLayer.addTo' not in src, f'{fname}: лишився hybridLayer.addTo (супутник дефолт)'
