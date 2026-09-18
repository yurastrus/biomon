"""Photo copyright notice: who owns the images shown in the viewer.

Camera-trap photos belong to the institution that runs the camera, not to the
platform. `photo_rights_holder` resolves that owner for the notice rendered
under every photo, and the tests below pin the two cases that matter: a
location shared by several institutions, and a location with no institution at
all (where the UI must fall back to a generic notice, never to an empty line).
"""
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / 'app'


@pytest.fixture
def rights_holder(app, db_session):
    from app.camera_traps.routes import photo_rights_holder
    return photo_rights_holder


def _link(ct_session, location_id, institution_id):
    from app.camera_traps.models import location_institutions
    ct_session.execute(location_institutions.insert().values(
        location_id=location_id, institution_id=institution_id))
    ct_session.commit()


def _institution(db_session, name_uk, name_en=None, code=None):
    from app.extensions import db
    from app.models import Institution
    inst = Institution(name_uk=name_uk, name_en=name_en, code=code)
    db.session.add(inst)
    db.session.commit()
    return inst


def test_rights_holder_names_the_owning_institution(
        rights_holder, db_session, ct_session, make_ct_location):
    loc = make_ct_location(name='Camera 1')
    inst = _institution(db_session, 'Природний заповідник «Розточчя»',
                        'Roztochya Nature Reserve', 'RSNR')
    _link(ct_session, loc.id, inst.id)

    assert rights_holder(ct_session, loc.id, lang='uk') == \
        'Природний заповідник «Розточчя»'


def test_rights_holder_uses_english_name_for_en(
        rights_holder, db_session, ct_session, make_ct_location):
    loc = make_ct_location(name='Camera 1')
    inst = _institution(db_session, 'Природний заповідник «Розточчя»',
                        'Roztochya Nature Reserve', 'RSNR')
    _link(ct_session, loc.id, inst.id)

    assert rights_holder(ct_session, loc.id, lang='en') == \
        'Roztochya Nature Reserve'


def test_rights_holder_lists_every_owner_of_a_shared_location(
        rights_holder, db_session, ct_session, make_ct_location):
    loc = make_ct_location(name='Shared camera')
    first = _institution(db_session, 'Алешківські піски', code='OLSH')
    second = _institution(db_session, 'Біосферний заповідник', code='BIOS')
    _link(ct_session, loc.id, first.id)
    _link(ct_session, loc.id, second.id)

    label = rights_holder(ct_session, loc.id, lang='uk')
    assert 'Алешківські піски' in label and 'Біосферний заповідник' in label


def test_rights_holder_is_none_without_institution(
        rights_holder, ct_session, make_ct_location):
    """No owner recorded -> None, so the UI shows the generic notice."""
    loc = make_ct_location(name='Orphan camera')
    assert rights_holder(ct_session, loc.id) is None
    assert rights_holder(ct_session, None) is None


@pytest.mark.parametrize('template', [
    'camera_traps/templates/gallery.html',
    'camera_traps/templates/identification.html',
    'camera_traps/templates/photo_viewer.html',
])
def test_photo_pages_render_the_copyright_notice(template):
    text = (TEMPLATES / template).read_text(encoding='utf-8')
    assert 'photo-copyright' in text
    assert 'Копіювання і поширення заборонене' in text


# --- Basemap attribution ----------------------------------------------------
# OSM (ODbL) and Esri World Imagery both require visible credit. A map added
# later without it is a licence breach nobody notices, so the check is a test.

OSM_LAYER = re.compile(
    r"L\.tileLayer\('https://\{s\}\.tile\.openstreetmap\.org"
    r"/\{z\}/\{x\}/\{y\}\.png'\s*,\s*\{[^{}]*\}", re.S)
ESRI_LAYER = re.compile(
    r"L\.tileLayer\('https://server\.arcgisonline\.com/ArcGIS/rest/services/"
    r"World_Imagery/MapServer/tile/\{z\}/\{y\}/\{x\}'\s*,\s*\{[^{}]*\}", re.S)


def _map_templates():
    return sorted(
        p for p in TEMPLATES.rglob('*.html')
        if 'L.tileLayer(' in p.read_text(encoding='utf-8'))


def test_every_osm_basemap_is_attributed():
    offenders = []
    for path in _map_templates():
        text = path.read_text(encoding='utf-8')
        for match in OSM_LAYER.findall(text):
            if 'openstreetmap.org/copyright' not in match:
                offenders.append(path.name)
    assert not offenders, f"OSM layer without attribution in: {offenders}"


def test_every_satellite_basemap_is_attributed():
    offenders = []
    for path in _map_templates():
        text = path.read_text(encoding='utf-8')
        for match in ESRI_LAYER.findall(text):
            if 'Esri' not in match or 'attribution' not in match:
                offenders.append(path.name)
    assert not offenders, f"Esri imagery without attribution in: {offenders}"
