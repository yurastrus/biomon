"""
#44: all PAM maps must have an OSM<->Satellite toggle, default OSM.

Regression guard on the template contract (verified visually in prod).
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
    assert 'World_Imagery' in src, f'{fname}: no satellite layer'
    assert 'L.control.layers' in src, f'{fname}: no layer toggle'


@pytest.mark.parametrize('fname', MAPS)
def test_pam_map_defaults_to_osm(fname):
    src = (PAM_TPL / fname).read_text(encoding='utf-8')
    # OSM is added to the map by default (osm / osmLayer .addTo(map))
    assert re.search(r'osm\w*\.addTo\(map\)', src, re.IGNORECASE), \
        f'{fname}: OSM is not the default base layer'
    # satellite/hybrid is NOT added directly -- only via the toggle
    assert not re.search(r'(hybrid|satellite)\w*\.addTo\(map\)', src, re.IGNORECASE), \
        f'{fname}: satellite added by default instead of OSM'
