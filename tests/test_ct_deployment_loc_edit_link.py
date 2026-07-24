"""
From the deployments map you must be able to jump to editing a location.

Mirrors the data_quality pattern (marker popup with an edit link). The
manage-deployments markers bind a popup linking to manage-locations, and
manage-locations must honour the ?location_id= deep link to preselect it.
"""
import pathlib

CT_TPL = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'camera_traps' / 'templates'
DEP = (CT_TPL / 'manage_deployments.html').read_text(encoding='utf-8')
LOC = (CT_TPL / 'manage_locations.html').read_text(encoding='utf-8')


def test_deployment_markers_bind_popup():
    assert 'locPopupHtml' in DEP, 'немає функції popup для маркерів локацій'
    assert '.bindPopup(locPopupHtml(loc))' in DEP, 'popup не прив\'язаний до маркерів'


def test_popup_links_to_location_edit():
    # Link targets manage-locations with the location id as a deep-link param.
    assert 'camera-traps/manage-locations?location_id=' in DEP, \
        'popup не веде на редагування локації'
    assert "target=\"_blank\"" in DEP, 'посилання не відкривається в новій вкладці'


def test_location_edit_link_gated_by_manager_role():
    # QC-only users can't reach manage-locations → link must be behind canEditLoc.
    assert 'canEditLoc' in DEP, 'посилання не захищене прапорцем прав (canEditLoc)'


def test_manage_locations_honours_deep_link():
    assert "get('location_id')" in LOC, 'manage-locations не читає ?location_id='
    assert 'selectLocation(Number(pLoc))' in LOC, \
        'manage-locations не предвибирає локацію з deep-link'
