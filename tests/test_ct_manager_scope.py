# SPDX-License-Identifier: AGPL-3.0-only
"""Camera traps: manager rights are per institution (since 2026-10-27).

The person under test, ``split``, holds the global ``manager`` role, can see
parks A and B, but manages only park A. Before the split that was impossible:
seeing B made them its manager too. Every manager action on a B location must
now be refused, and every one on an A location must keep working.

Real SQLite sessions on both sides (main DB through ``db_session``, ct_db through
``ct_session``), so the location ↔ institution joins are the real queries.
"""
from datetime import date, datetime, timedelta
import json
import re
from unittest.mock import patch

import pytest


@pytest.fixture
def parks(db_session):
    from app.models import Institution
    a = Institution(name_uk='Парк А', name_en='Park A', code='PA')
    b = Institution(name_uk='Парк Б', name_en='Park B', code='PB')
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


@pytest.fixture
def locs(ct_session, parks, make_ct_location):
    from app.camera_traps.models import location_institutions
    a, b = parks
    loc_a = make_ct_location(name='Локація А', latitude=49.1, longitude=24.1)
    loc_b = make_ct_location(name='Локація Б', latitude=49.2, longitude=24.2)
    ct_session.execute(location_institutions.insert(), [
        {'location_id': loc_a.id, 'institution_id': a.id},
        {'location_id': loc_b.id, 'institution_id': b.id},
    ])
    ct_session.commit()
    return loc_a, loc_b


def _link(inst_id, manage=False):
    from app.models import UserInstitution
    return UserInstitution(institution_id=inst_id, can_view_ct=True, can_view_pam=True,
                           can_manage_ct=manage, can_manage_pam=manage)


@pytest.fixture
def split(db_session, make_user, parks):
    """Manager role; sees A and B, manages A only."""
    a, b = parks
    u = make_user(username='split', roles=('manager',))
    u.institution_links.extend([_link(a.id, manage=True), _link(b.id)])
    db_session.commit()
    return u


@pytest.fixture
def client_as(app, db_session):
    def _as(user):
        # One app context for the whole test: drop the cached login, or the
        # second client would act as the first (see CLAUDE.md, multi-client tests).
        from flask import g
        g.pop('_login_user', None)
        cl = app.test_client()
        with cl.session_transaction() as s:
            s['_user_id'] = str(user.id)
            s['_fresh'] = True
        return cl
    return _as


@pytest.fixture(autouse=True)
def _ct_db(ct_session):
    """Routes read ct_db through get_ct_session(); point it at the SQLite one."""
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield


def _batch(ct_session, batch_id, location, user):
    from app.camera_traps.models import UploadBatch
    ct_session.add(UploadBatch(id=batch_id, location_id=location.id,
                               uploaded_by_id=user.id, status='uploading'))
    ct_session.commit()


def test_service_plan_uses_ct_scope_and_forecast(
        client_as, split, locs, parks, ct_session, make_ct_deployment):
    from app.camera_traps.models import (
        BatteryType, LocationStats, ServiceVisit, VisitPurpose, location_institutions,
    )

    loc_a, _ = locs
    a, b = parks
    today = date.today()
    for model in (LocationStats, BatteryType, VisitPurpose, ServiceVisit):
        model.__table__.create(ct_session.get_bind())
    ct_session.execute(location_institutions.insert().values(location_id=loc_a.id, institution_id=b.id))
    make_ct_deployment(location=loc_a, start_date=today - timedelta(days=70),
                       end_date=None, camera_model='Browning', camera_id='0405')
    ct_session.add_all([
        VisitPurpose(id=1, name_ua='Плановий', name_en='Routine'),
        BatteryType(id=1, name_ua='Літієві', name_en='Lithium'),
        LocationStats(location_id=loc_a.id, avg_photos_per_day=200),
        ServiceVisit(location_id=loc_a.id, user_id=split.id, visit_purpose_id=1,
                     visit_datetime=datetime.combine(today - timedelta(days=60), datetime.min.time()),
                     battery_type_id=1, sd_card_changed=True),
        ServiceVisit(location_id=loc_a.id, user_id=split.id, visit_purpose_id=1,
                     visit_datetime=datetime.combine(today - timedelta(days=30), datetime.min.time()),
                     photos_on_card=0, is_camera_operational=True),
    ])
    ct_session.commit()
    response = client_as(split).get('/en/camera-traps/service-plan')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    items = json.loads(re.search(r'const ITEMS = (\[.*?\]);$', html, re.M).group(1))
    assert len(items) == 1
    row = items[0]
    assert row['key'] == f'ct:{loc_a.id}' and row['module'] == 'ct'
    assert row['inst_ids'] == [a.id]
    assert row['device'] == 'Browning · 0405'
    assert row['status'] == 'warning'
    assert row['visit_by_iso'] == (today + timedelta(days=20)).isoformat()
    assert row['limiting'] == 'photos'
    assert row['predicted_photos'] == 6000
    assert row['battery'] == 'Lithium'
    assert row['battery_since'] == row['card_since']
    assert row['photos_on_card'] == 0
    assert row['battery_days_left'] is None and row['sd_card_days_left'] is None
    assert 'Service plan' in html


def test_service_plan_is_closed_to_verifiers(client_as, make_user):
    verifier = make_user(username='ct_plan_verifier', roles=('ct_verifier',))
    assert client_as(verifier).get('/uk/camera-traps/service-plan').status_code == 302


@pytest.mark.parametrize('end_offset,operational,expected', [
    (-1, True, 'removed'),
    (2, True, 'critical'),
    (10, True, 'warning'),
    (None, False, 'stopped'),
    (None, True, 'overdue'),
])
def test_service_plan_deployment_and_operational_states(end_offset, operational, expected):
    from types import SimpleNamespace
    from app.camera_traps.service_plan import _plan_row

    today = date(2026, 11, 14)
    start = today - timedelta(days=40 if end_offset is not None else 301)
    end = today + timedelta(days=end_offset) if end_offset is not None else None
    deployment = SimpleNamespace(start_date=start, end_date=end, camera_model=None, camera_id=None)
    loc = SimpleNamespace(id=1, name='Camera', latitude=49, longitude=24)
    visit = SimpleNamespace(visit_datetime=datetime.combine(start, datetime.min.time()),
                            visit_purpose_id=1, is_camera_operational=operational,
                            battery_type=None, sd_card_changed=False, photos_on_card=None)
    row = _plan_row(loc, None, [], [visit], deployment, None, 'uk', today)
    assert row['status'] == expected
    if end_offset is not None and end_offset > 0:
        assert row['visit_by_iso'] == end.isoformat()
        assert row['limiting'] == 'deployment_end'


# ── model and helpers ───────────────────────────────────────────────────────

def test_manage_ids_need_flag_view_and_role(db_session, split, parks):
    a, b = parks
    assert split.manage_institution_ids('ct') == [a.id]
    assert split.manage_institution_ids('pam') == [a.id]
    # Seeing B is still seeing B.
    assert sorted(split.allowed_institution_ids('ct')) == sorted([a.id, b.id])


def test_losing_the_role_switches_every_flag_off(db_session, split):
    split.roles = [r for r in split.roles if r.name != 'manager']
    db_session.commit()
    assert split.manage_institution_ids('ct') == []


def test_manage_flag_without_view_does_not_count(db_session, make_user, parks):
    from app.models import UserInstitution
    a, _ = parks
    u = make_user(username='odd', roles=('manager',))
    u.institution_links.append(UserInstitution(
        institution_id=a.id, can_view_ct=False, can_manage_ct=True,
        can_view_pam=True))
    db_session.commit()
    assert u.manage_institution_ids('ct') == []


def test_access_helper_falls_back_for_a_host_without_the_split():
    """myproject's User has no manage_institution_ids: old meaning applies."""
    from app.camera_traps import access

    class Inst:
        def __init__(self, i):
            self.id = i

    class OldHostUser:
        is_authenticated = True
        institutions = [Inst(1), Inst(2)]

        def __init__(self, roles):
            self._roles = roles

        def has_role(self, *names):
            return any(n in self._roles for n in names)

    assert access.manage_institution_ids(OldHostUser({'manager'})) == [1, 2]
    assert access.manage_institution_ids(OldHostUser({'ct_verifier'})) == []
    assert access.can_manage_institutions(OldHostUser({'admin'}), [99]) is True


def test_any_of_the_locations_institutions_is_enough(app, split, parks):
    from app.camera_traps import access
    a, b = parks
    assert access.can_manage_institutions(split, [b.id, a.id]) is True
    assert access.can_manage_institutions(split, [b.id]) is False
    assert access.can_manage_institutions(split, []) is False


# ── upload ──────────────────────────────────────────────────────────────────

def _create_batch(cl, location_id):
    with patch('app.camera_traps.utils.create_upload_batch', return_value='b-new') as cub, \
         patch('app.camera_traps.background_tasks.get_storage_disk_usage',
               return_value={'free_bytes': 100 * 1024 ** 3}), \
         patch('app.camera_traps.ai_runner.pause_ai_classification'):
        resp = cl.post('/uk/camera-traps/api/create-batch',
                       json={'location_id': location_id, 'total_files': 1})
    return resp, cub


def test_create_batch_on_a_managed_location(client_as, split, locs):
    resp, cub = _create_batch(client_as(split), locs[0].id)
    assert resp.status_code == 201
    assert cub.called


def test_create_batch_on_a_location_only_seen_is_refused(client_as, split, locs):
    resp, cub = _create_batch(client_as(split), locs[1].id)
    assert resp.status_code == 403
    assert not cub.called


def test_admin_creates_batch_anywhere(client_as, make_user, locs):
    admin = make_user(username='adm', roles=('admin',))
    resp, cub = _create_batch(client_as(admin), locs[1].id)
    assert resp.status_code == 201


def _process(cl, batch_id, location_id):
    import io
    with patch('app.camera_traps.utils.process_single_photo', return_value=1) as proc:
        resp = cl.post('/uk/camera-traps/upload/process-single', data={
            'location_id': str(location_id), 'batch_id': batch_id,
            'save_original': 'false',
            'file': (io.BytesIO(b'x'), 'IMG_0001.JPG'),
        }, content_type='multipart/form-data')
    return resp, proc


def test_process_single_into_a_foreign_batch_is_refused(client_as, ct_session, split, locs):
    _batch(ct_session, 'batch-b', locs[1], split)
    resp, proc = _process(client_as(split), 'batch-b', locs[1].id)
    assert resp.status_code == 403
    assert not proc.called


def test_process_single_cannot_retarget_a_batch_to_another_location(
        client_as, ct_session, split, locs):
    _batch(ct_session, 'batch-a', locs[0], split)
    resp, proc = _process(client_as(split), 'batch-a', locs[1].id)
    assert resp.status_code == 400
    assert not proc.called


def test_process_single_into_an_own_batch(client_as, ct_session, split, locs):
    _batch(ct_session, 'batch-a', locs[0], split)
    resp, proc = _process(client_as(split), 'batch-a', locs[0].id)
    assert resp.status_code != 403
    assert proc.called


def test_batch_endpoints_refuse_a_foreign_batch(client_as, ct_session, split, locs):
    _batch(ct_session, 'batch-b', locs[1], split)
    cl = client_as(split)
    assert cl.get('/uk/camera-traps/api/batch/batch-b/uploaded-files').status_code == 403
    assert cl.get('/uk/camera-traps/api/batch-status/batch-b').status_code == 403
    with patch('app.camera_traps.fast_upload.start_async_grouping') as grouping:
        resp = cl.post('/uk/camera-traps/api/finalize-batch-async', json={'batch_id': 'batch-b'})
    assert resp.status_code == 403
    assert not grouping.called


def test_upload_page_offers_only_managed_locations(client_as, split, locs):
    html = client_as(split).get('/uk/camera-traps/upload-fast').get_data(as_text=True)
    assert 'Локація А' in html
    assert 'Локація Б' not in html


def test_new_location_must_belong_to_a_managed_institution(client_as, split, parks, locs):
    a, b = parks
    cl = client_as(split)
    refused = cl.post('/uk/camera-traps/api/create-location', json={
        'name': 'Нова', 'latitude': 49.9, 'longitude': 24.9, 'institution_id': b.id})
    assert refused.status_code == 403
    no_inst = cl.post('/uk/camera-traps/api/create-location', json={
        'name': 'Нова', 'latitude': 49.9, 'longitude': 24.9})
    assert no_inst.status_code == 400
    ok = cl.post('/uk/camera-traps/api/create-location', json={
        'name': 'Нова', 'latitude': 49.9, 'longitude': 24.9, 'institution_id': a.id})
    assert ok.status_code == 201


def test_admin_cannot_create_an_institutionless_location(auth_client, parks):
    response = auth_client(role='admin').post('/uk/camera-traps/api/create-location', json={
        'name': 'Без установи', 'latitude': 48.7, 'longitude': 25.1})
    assert response.status_code == 400
    assert 'установу або проєкт' in response.get_json()['message']


def test_upload_pages_require_an_institution_before_creating_a_location():
    from pathlib import Path
    templates = Path(__file__).resolve().parent.parent / 'app' / 'camera_traps' / 'templates'
    for name in ('upload.html', 'upload_fast.html', 'upload_video.html'):
        html = (templates / name).read_text(encoding='utf-8')
        assert "const institutionId = $('#institution-select').val();" in html
        assert "if (!institutionId)" in html
        assert 'institution_id: institutionId' in html


def test_all_upload_pages_sync_an_existing_location_back_to_its_institution():
    from pathlib import Path
    templates = Path(__file__).resolve().parent.parent / 'app' / 'camera_traps' / 'templates'
    for name in ('upload.html', 'upload_fast.html', 'upload_video.html'):
        html = (templates / name).read_text(encoding='utf-8')
        assert 'loc.institution_ids' in html
        assert "$('#institution-select').val(" in html
        assert ".trigger('change')" in html


def test_a_verifier_can_no_longer_create_locations(client_as, make_user, parks):
    verifier = make_user(username='ver', roles=('ct_verifier',))
    resp = client_as(verifier).post('/uk/camera-traps/api/create-location', json={
        'name': 'Нова', 'latitude': 49.9, 'longitude': 24.9, 'institution_id': parks[0].id})
    assert resp.status_code in (302, 403)


# ── locations, imports, favourites ──────────────────────────────────────────

def test_update_location_only_where_managed(client_as, split, locs):
    resp = client_as(split).post(f'/uk/camera-traps/api/update-location/{locs[1].id}',
                                 json={'name': 'Перейменовано'})
    assert resp.status_code == 403


def test_validity_toggle_only_where_managed(client_as, split, locs):
    resp = client_as(split).post(f'/uk/camera-traps/location/{locs[1].id}/validity',
                                 json={'is_valid': False})
    assert resp.status_code == 403


def test_quality_control_keeps_full_location_access(app, make_user, locs, ct_session):
    from flask_login import login_user
    from app.camera_traps.routes import _user_can_access_location
    qc = make_user(username='qc', roles=('quality_control',))
    with app.test_request_context():
        login_user(qc)
        assert _user_can_access_location(ct_session, locs[1].id) is True


def test_classification_import_only_into_a_managed_location(client_as, split, locs):
    import io
    resp = client_as(split).post('/uk/camera-traps/import-classification/preview', data={
        'location_id': str(locs[1].id),
        'file': (io.BytesIO(b'filename,prediction\n'), 'x.csv'),
    }, content_type='multipart/form-data')
    assert resp.status_code == 403


def test_favourites_are_curated_by_the_photos_own_manager(
        client_as, ct_session, split, locs, make_ct_observation, make_ct_photo):
    obs_b = make_ct_observation(location=locs[1])
    photo_b = make_ct_photo(observation=obs_b, is_favorite=True)
    obs_a = make_ct_observation(location=locs[0])
    photo_a = make_ct_photo(observation=obs_a, is_favorite=True)
    cl = client_as(split)
    assert cl.post('/uk/camera-traps/api/gallery/remove-favorite',
                   json={'photo_id': photo_b.id}).status_code == 403
    assert cl.post('/uk/camera-traps/api/gallery/remove-favorite',
                   json={'photo_id': photo_a.id}).status_code == 200


# ── moderation and the hub ──────────────────────────────────────────────────

def test_moderator_power_is_per_location(app, split, locs, ct_session, make_user):
    from flask_login import login_user
    from app.camera_traps.routes import _user_can_manage_location
    with app.test_request_context():
        login_user(split)
        assert _user_can_manage_location(ct_session, locs[0].id) is True
        assert _user_can_manage_location(ct_session, locs[1].id) is False
        assert _user_can_manage_location(ct_session, None) is False
    admin = make_user(username='adm2', roles=('admin',))
    with app.test_request_context():
        login_user(admin)
        assert _user_can_manage_location(ct_session, locs[1].id) is True


def test_review_mode_needs_a_managed_institution(client_as, make_user, parks):
    from app.models import UserInstitution
    a, _ = parks
    seer = make_user(username='seer', roles=('manager',))
    seer.institution_links.append(UserInstitution(institution_id=a.id, can_view_ct=True))
    resp = client_as(seer).get(
        '/uk/camera-traps/api/next-observation-for-identification?review=true')
    assert resp.status_code == 403


def test_hub_shows_manager_cards_only_to_a_manager_of_something(client_as, split, make_user):
    assert b'upload-fast' in client_as(split).get('/uk/camera-traps/').data
    idle = make_user(username='idle_manager', roles=('manager',))
    assert b'upload-fast' not in client_as(idle).get('/uk/camera-traps/').data
