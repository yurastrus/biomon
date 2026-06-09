"""
#44: усі карти PAM мають перемикач OSM↔Супутник, дефолт — OSM.

Regression-guard на контракт шаблонів (візуально перевіряється на проді).
"""
import re
import pathlib

import pytest

PAM_TPL = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'pam' / 'templates'
MAPS = [
    'pam_overview.html',
    'pam_species_detailed.html',
    'pam_service_log.html',
    'manage_pam_locations.html',
    'pam_import.html',
]


@pytest.mark.parametrize('fname', MAPS)
def test_pam_map_has_osm_satellite_toggle(fname):
    src = (PAM_TPL / fname).read_text(encoding='utf-8')
    assert 'World_Imagery' in src, f'{fname}: немає супутникового шару'
    assert 'L.control.layers' in src, f'{fname}: немає перемикача шарів'


@pytest.mark.parametrize('fname', MAPS)
def test_pam_map_defaults_to_osm(fname):
    src = (PAM_TPL / fname).read_text(encoding='utf-8')
    # OSM додається на карту за замовчуванням (osm / osmLayer .addTo(map))
    assert re.search(r'osm\w*\.addTo\(map\)', src, re.IGNORECASE), \
        f'{fname}: OSM не є дефолтним базовим шаром'
    # супутник/hybrid НЕ додається напряму — лише через перемикач
    assert not re.search(r'(hybrid|satellite)\w*\.addTo\(map\)', src, re.IGNORECASE), \
        f'{fname}: супутник доданий за замовчуванням замість OSM'
