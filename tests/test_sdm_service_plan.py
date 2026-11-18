"""The combined field plan preserves the independent CT and PAM access scopes."""
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


PATH = "/en/sdm/service-plan"


def _items(response):
    html = response.get_data(as_text=True)
    config = re.search(r'<script type="application/json" id="svp-config">\s*(.*?)\s*</script>', html, re.S)
    return json.loads(config.group(1))["items"]


@pytest.mark.parametrize(("role", "status"), [(None, 302), ("ct_verifier", 403), ("pam_verifier", 403)])
def test_service_plan_protected(client, auth_client, role, status):
    client = auth_client(role=role) if role else client
    with (patch("app.camera_traps.database.get_ct_session") as ct,
          patch("app.pam.routes.get_pam_db_connection") as pam):
        assert client.get(PATH).status_code == status
        ct.assert_not_called()
        pam.assert_not_called()


def test_service_plan_manager_without_scope_does_not_query_monitoring_databases(auth_client):
    client = auth_client(role="manager")
    with (patch("app.camera_traps.database.get_ct_session") as ct,
          patch("app.pam.routes.get_pam_db_connection") as pam):
        response = client.get(PATH)
        assert response.status_code == 200
        assert "No locations in the institutions you manage." in response.get_data(as_text=True)
        ct.assert_not_called()
        pam.assert_not_called()


def test_service_plan_combines_rows_with_independent_scopes(
    auth_client, db_session, ct_session, make_ct_location,
):
    from app.camera_traps.models import BatteryType, LocationStats, ServiceVisit, VisitPurpose, location_institutions
    from app.models import Institution, User, UserInstitution

    client = auth_client(role="manager")
    manager = User.query.filter_by(username="test_manager").one()
    ct_park = Institution(name_uk="Парк CT", name_en="CT park", code="CT")
    pam_park = Institution(name_uk="Парк PAM", name_en="PAM park", code="PAM")
    hidden = Institution(name_uk="Інший парк", name_en="View-only park", code="VIEW")
    db_session.add_all([ct_park, pam_park, hidden])
    db_session.flush()
    for institution in (ct_park, pam_park, hidden):
        manager.institution_links.append(UserInstitution(
            institution_id=institution.id, can_view_ct=True, can_view_pam=True,
            can_manage_ct=institution == ct_park, can_manage_pam=institution == pam_park,
        ))
    db_session.commit()

    for model in (LocationStats, BatteryType, VisitPurpose, ServiceVisit):
        model.__table__.create(ct_session.get_bind())
    allowed_ct = make_ct_location(name="Camera", latitude=49.1, longitude=23.9)
    other_ct = make_ct_location(name="PAM park camera")
    ct_session.execute(location_institutions.insert(), [
        {"location_id": allowed_ct.id, "institution_id": ct_park.id},
        {"location_id": allowed_ct.id, "institution_id": hidden.id},
        {"location_id": other_ct.id, "institution_id": pam_park.id},
    ])
    ct_session.commit()

    # Same numeric ID and coordinates as the camera, with a different PAM scope.
    recorder = SimpleNamespace(location_id=allowed_ct.id, location_name="Реєстратор",
                               location_name_en="Recorder", lat=49.1, lon=23.9,
                               inst_ids=[pam_park.id, ct_park.id, hidden.id])
    conn = MagicMock()
    conn.execute.side_effect = [
        MagicMock(**{"fetchall.return_value": rows}) for rows in ([recorder], [], [], [])
    ]
    with (patch("app.camera_traps.database.get_ct_session", return_value=ct_session),
          patch("app.camera_traps.database.close_ct_session") as close_ct,
          patch("app.pam.routes.get_pam_db_connection", return_value=conn)):
        response = client.get(PATH)

    assert response.status_code == 200 and response.cache_control.no_store
    html = response.get_data(as_text=True)
    assert html.count('id="svp-map"') == 1
    assert 'value="other">' in html and 'value="other" checked' not in html
    assert 'Marker colors' in html
    assert '<li data-status="overdue">Overdue</li>' in html
    assert "Combined service plan" in html
    camera, pam = _items(response)
    assert (camera["key"], camera["name"], camera["inst_ids"]) == (f"ct:{allowed_ct.id}", "Camera", [ct_park.id])
    assert (pam["key"], pam["name"], pam["inst_ids"]) == (f"pam:{allowed_ct.id}", "Recorder", [pam_park.id])
    assert "CT park" in html and "PAM park" in html and "View-only park" not in html
    assert conn.execute.call_args_list[0].args[1] == {"inst_ids": [pam_park.id]}
    conn.close.assert_called_once()
    close_ct.assert_called_once()


def test_service_plan_admin_has_unrestricted_scope(auth_client):
    client = auth_client(role="admin")
    with (patch("app.camera_traps.database.get_ct_session"),
          patch("app.camera_traps.database.close_ct_session"),
          patch("app.camera_traps.service_plan.load_service_plan", return_value=[]) as ct,
          patch("app.pam.routes.load_pam_service_plan", return_value=[]) as pam):
        assert client.get(PATH).status_code == 200
    assert ct.call_args.kwargs["managed_ids"] is None
    assert pam.call_args.kwargs["managed_ids"] is None
    assert ct.call_args.args[1:] == pam.call_args.args
