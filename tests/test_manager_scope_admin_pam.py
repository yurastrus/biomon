# SPDX-License-Identifier: AGPL-3.0-only
"""Per-institution manager rights (2026-10-27): who may grant them, and PAM.

* Only an admin turns "manages this institution" on or off. A manager's form
  has no such column, and a crafted POST carrying ``manage_*`` changes nothing.
* A manager edits other people only inside the institutions they manage, and
  never wipes a person's access elsewhere (before the split the form rewrote
  every row, so a manager saving a colleague dropped the parks they could not
  see).
* The ``manager`` role itself is admin-only, whatever ``Role.assignable_by`` says.
* PAM: the manage page and the edit check use managed institutions only.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def parks(db_session):
    from app.models import Institution
    a = Institution(name_uk='Парк А', name_en='Park A', code='PA')
    b = Institution(name_uk='Парк Б', name_en='Park B', code='PB')
    c = Institution(name_uk='Парк В', name_en='Park C', code='PC')
    db_session.add_all([a, b, c])
    db_session.commit()
    return a, b, c


def _link(inst_id, manage=False, export=False):
    from app.models import UserInstitution
    return UserInstitution(institution_id=inst_id, can_view_ct=True, can_view_pam=True,
                           can_export_ct=export, can_export_pam=export,
                           can_manage_ct=manage, can_manage_pam=manage)


@pytest.fixture
def split(db_session, make_user, parks):
    """Manager role; sees A and B, manages A only."""
    a, b, _ = parks
    u = make_user(username='split', roles=('manager',))
    u.institution_links.extend([_link(a.id, manage=True), _link(b.id)])
    db_session.commit()
    return u


@pytest.fixture
def client_as(app, db_session):
    def _as(user):
        from flask import g
        g.pop('_login_user', None)
        cl = app.test_client()
        with cl.session_transaction() as s:
            s['_user_id'] = str(user.id)
            s['_fresh'] = True
        return cl
    return _as


def _flags(user, inst):
    link = next((l for l in user.institution_links if l.institution_id == inst.id), None)
    return None if link is None else link.module_flags(user)


# ── the service layer ───────────────────────────────────────────────────────

def test_manager_offers_only_managed_institutions(split, parks):
    from app.admin.services import UserService
    a, _, _ = parks
    assert [i.id for i in UserService.get_available_institutions(split)] == [a.id]


def test_manager_role_is_never_offered_to_a_manager(db_session, split, make_role):
    from app.admin.services import UserService
    role = make_role('manager')
    role.assignable_by = 'manager'          # even if misconfigured
    db_session.commit()
    names = {r.name for r in UserService.get_available_roles(split)}
    assert 'manager' not in names and 'admin' not in names


def test_crafted_manage_ticks_from_a_manager_are_ignored(db_session, split, parks, make_user):
    from app.admin.services import UserService
    a, _, _ = parks
    target = make_user(username='colleague', roles=('ct_verifier',))
    target.institution_links.append(_link(a.id))
    db_session.commit()

    UserService.update_user(
        target, UserService.get_available_roles(split),
        'colleague', None, None, None, None, None,
        module_access={'view_ct': {str(a.id)}, 'view_pam': {str(a.id)},
                       'manage_ct': {str(a.id)}, 'manage_pam': {str(a.id)}},
        selected_role_ids=[], editor=split)
    db_session.commit()
    assert _flags(target, a)['manage_ct'] is False
    assert _flags(target, a)['manage_pam'] is False


def test_manager_save_keeps_access_outside_their_parks(db_session, split, parks, make_user):
    from app.admin.services import UserService
    a, b, c = parks
    target = make_user(username='colleague', roles=('ct_verifier',))
    target.institution_links.extend([_link(a.id), _link(c.id, export=True)])
    db_session.commit()

    # The manager's form only shows A; they untick PAM there.
    UserService.update_user(
        target, UserService.get_available_roles(split),
        'colleague', None, None, None, None, None,
        module_access={'view_ct': {str(a.id)},
                       # a crafted tick for a park the manager does not manage
                       'view_pam': {str(c.id)}},
        selected_role_ids=[], editor=split)
    db_session.commit()

    assert _flags(target, a)['view_ct'] is True
    assert _flags(target, a)['view_pam'] is False
    kept = _flags(target, c)
    assert kept['view_ct'] and kept['view_pam'] and kept['export_ct']


def test_manager_cannot_grant_a_park_they_only_see(db_session, split, parks, make_user):
    from app.admin.services import UserService
    _, b, _ = parks
    target = make_user(username='newbie', roles=('ct_verifier',))
    UserService.update_user(
        target, UserService.get_available_roles(split),
        'newbie', None, None, None, None, None,
        module_access={'view_ct': {str(b.id)}}, selected_role_ids=[], editor=split)
    db_session.commit()
    assert _flags(target, b) is None


def test_manager_keeps_stored_manage_flags_of_a_colleague(db_session, split, parks, make_user):
    """A co-manager of park A, edited by another manager, stays a manager."""
    from app.admin.services import UserService
    a, _, _ = parks
    peer = make_user(username='peer', roles=('manager',))
    peer.institution_links.append(_link(a.id, manage=True))
    db_session.commit()

    UserService.update_user(
        peer, UserService.get_available_roles(split),
        'peer', None, None, None, None, None,
        module_access={'view_ct': {str(a.id)}, 'view_pam': {str(a.id)}},
        selected_role_ids=[], editor=split)
    db_session.commit()
    assert peer.manage_institution_ids('ct') == [a.id]


def test_admin_grants_and_revokes_manage(db_session, parks, make_user):
    from app.admin.services import UserService
    from app.models import Role
    a, b, _ = parks
    admin = make_user(username='adm', roles=('admin',))
    target = make_user(username='future', roles=('manager',))
    all_roles = Role.query.all()
    keep_manager = [str(r.id) for r in target.roles]

    UserService.update_user(
        target, all_roles, 'future', None, None, None, None, None,
        module_access={'view_ct': {str(a.id), str(b.id)}, 'view_pam': {str(a.id)},
                       'manage_ct': {str(a.id), str(b.id)},
                       # manage without access in that module is dropped
                       'manage_pam': {str(b.id)}},
        selected_role_ids=keep_manager, editor=admin)
    db_session.commit()
    assert sorted(target.manage_institution_ids('ct')) == sorted([a.id, b.id])
    assert target.manage_institution_ids('pam') == []

    UserService.update_user(
        target, all_roles, 'future', None, None, None, None, None,
        module_access={'view_ct': {str(a.id), str(b.id)}, 'manage_ct': {str(a.id)}},
        selected_role_ids=keep_manager, editor=admin)
    db_session.commit()
    assert target.manage_institution_ids('ct') == [a.id]


def test_admin_without_manager_role_keeps_stored_flags(db_session, parks, make_user):
    """Unticking the role does not wipe the per-park flags (like export)."""
    from app.admin.services import UserService
    from app.models import Role
    a, _, _ = parks
    admin = make_user(username='adm', roles=('admin',))
    target = make_user(username='paused', roles=('manager',))
    target.institution_links.append(_link(a.id, manage=True))
    db_session.commit()

    UserService.update_user(
        target, Role.query.all(), 'paused', None, None, None, None, None,
        module_access={'view_ct': {str(a.id)}, 'view_pam': {str(a.id)}},
        selected_role_ids=[], editor=admin)
    db_session.commit()
    assert target.manage_institution_ids('ct') == []      # role gone
    assert _flags(target, a)['manage_ct'] is True         # flag remembered


def test_can_edit_needs_a_managed_shared_park(db_session, split, parks, make_user):
    from app.admin.services import UserService
    _, b, _ = parks
    only_b = make_user(username='only_b', roles=('ct_verifier',))
    only_b.institution_links.append(_link(b.id))
    db_session.commit()
    allowed, _msg = UserService.can_edit(split, only_b)
    assert allowed is False


def test_verification_queue_uses_managed_parks(db_session, split, parks):
    from app.admin.services import VerificationRequestService as Service
    a, b, _ = parks
    assert Service._decider_institution_ids(split) == {a.id}
    assert Service._decider_institution_ids(split, module='ct') == {a.id}


# ── the form ────────────────────────────────────────────────────────────────

def test_manage_column_is_admin_only(client_as, db_session, split, parks, make_user):
    target = make_user(username='colleague', roles=('ct_verifier',))
    target.institution_links.append(_link(parks[0].id))
    db_session.commit()

    as_manager = client_as(split).get(f'/uk/admin/users/edit/{target.id}')
    assert as_manager.status_code == 200
    assert b'name="manage_ct"' not in as_manager.data

    admin = make_user(username='adm', roles=('admin',))
    as_admin = client_as(admin).get(f'/uk/admin/users/edit/{target.id}')
    assert b'name="manage_ct"' in as_admin.data
    assert b'name="manage_pam"' in as_admin.data


def test_admin_form_post_sets_the_flag(client_as, db_session, parks, make_user):
    from app.models import Role, User
    a, _, _ = parks
    admin = make_user(username='adm', roles=('admin',))
    target = make_user(username='to_manage', roles=('manager',))
    target.email = 'to_manage@example.org'
    db_session.commit()
    manager_role = Role.query.filter_by(name='manager').one()

    resp = client_as(admin).post(f'/uk/admin/users/edit/{target.id}', data={
        'username': 'to_manage', 'email': 'to_manage@example.org',
        'roles': [str(manager_role.id)],
        'view_ct': [str(a.id)], 'manage_ct': [str(a.id)],
    })
    assert resp.status_code == 302
    assert User.query.get(target.id).manage_institution_ids('ct') == [a.id]


# ── PAM ─────────────────────────────────────────────────────────────────────

def _pam_rows(a, b):
    def row(lid, name, inst):
        r = MagicMock()
        r.location_id, r.location_name, r.location_name_en = lid, name, name
        r.lat, r.lon, r.institution_id, r.state_province = 49.0, 24.0, inst, ''
        return r
    return [row(1, 'ПАМ локація А', a.id), row(2, 'ПАМ локація Б', b.id)]


def _pam_manage_conn(rows):
    conn = MagicMock()
    first = MagicMock()
    first.fetchall.return_value = rows
    empty = MagicMock()
    empty.fetchall.return_value = []
    conn.execute.side_effect = [first, empty, empty, empty, empty, empty]
    return conn


def test_pam_manage_page_lists_only_managed_locations(client_as, split, parks):
    a, b, _ = parks
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_pam_manage_conn(_pam_rows(a, b))):
        resp = client_as(split).get('/uk/pam/manage-locations')
    assert resp.status_code == 200
    assert 'ПАМ локація А'.encode() in resp.data
    assert 'ПАМ локація Б'.encode() not in resp.data


def test_pam_edit_check_asks_with_managed_ids(app, split, parks):
    from flask_login import login_user
    from app.pam.routes import _user_can_edit_pam_location
    a, _, _ = parks
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    with app.test_request_context():
        login_user(split)
        _user_can_edit_pam_location(conn, 2)
    params = conn.execute.call_args.args[1]
    assert params['inst_ids'] == [a.id]


def test_pam_location_details_refused_where_not_managed(client_as, split):
    conn = MagicMock()
    loc = MagicMock()
    no_access = MagicMock()
    no_access.fetchone.return_value = None
    found = MagicMock()
    found.fetchone.return_value = loc
    conn.execute.side_effect = [found, no_access]
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        resp = client_as(split).get('/uk/pam/api/location/2')
    assert resp.status_code == 403


def test_pam_hub_hides_manager_cards_from_an_idle_manager(client_as, make_user, split):
    with patch('app.pam.routes.get_pam_db_connection', side_effect=Exception('no db')):
        idle = make_user(username='idle', roles=('manager',))
        idle_page = client_as(idle).get('/uk/pam')
        busy_page = client_as(split).get('/uk/pam')
    assert b'manage-locations' not in idle_page.data
    assert b'manage-locations' in busy_page.data


# ── notification recipients ─────────────────────────────────────────────────

def test_only_managers_of_the_named_park_are_notified(app, db_session, parks, make_user, split):
    from datetime import datetime
    from app.models import VerificationRequest, VerificationRequestInstitution
    from app.utils.emails import new_request_recipients as recipients
    _, b, _ = parks
    split.email = 'split@example.org'
    applicant = make_user(username='applicant', roles=('viewer',))
    applicant.email_confirmed_at = datetime.utcnow()
    req = VerificationRequest(user_id=applicant.id, module='ct')
    db_session.add(req)
    db_session.flush()
    db_session.add(VerificationRequestInstitution(request_id=req.id, institution_id=b.id))
    db_session.commit()
    with app.test_request_context():
        assert 'split@example.org' not in recipients(applicant, admin_email=None)


# ── access by institution (/admin/institution-access) ───────────────────────

def test_classify_reads_effective_rights(db_session, make_user, parks):
    from app.admin.services import InstitutionAccessService as S
    from app.models import UserInstitution
    a, _, _ = parks
    analyst = make_user(username='an', roles=('analyst',))   # analyst ⊃ ct_verifier
    link = UserInstitution(institution_id=a.id, can_view_ct=True, can_export_ct=True,
                           can_view_pam=True, can_manage_ct=True)
    caps = S.classify(analyst, link)
    assert caps['manage'] is None                 # flag without the manager role
    assert caps['verify_ct'] == 'ct'
    assert caps['verify_pam'] is None             # no pam_verifier role
    assert caps['export'] == 'ct'
    assert caps['view_only'] is None

    viewer = make_user(username='vw', roles=('viewer',))
    caps = S.classify(viewer, UserInstitution(institution_id=a.id, can_view_pam=True,
                                              can_export_pam=True))
    assert caps['export'] is None                 # export flag, no export role
    assert caps['view_only'] == 'pam'


def test_matrix_for_a_manager_lists_managed_parks_and_gaps(db_session, split, parks, make_user):
    from app.admin.services import InstitutionAccessService as S
    a, b, _ = parks
    ver = make_user(username='ver', roles=('ct_verifier',))
    ver.institution_links.append(_link(a.id))
    gone = make_user(username='gone', roles=('ct_verifier',))
    gone.is_active = False
    gone.institution_links.append(_link(a.id))
    db_session.commit()

    rows = S.matrix(split)
    assert [r['institution'].id for r in rows] == [a.id]
    cells = rows[0]['cells']
    assert [u.username for u, _k in cells['manage']] == ['split']
    assert {u.username for u, _k in cells['verify_ct']} == {'split', 'ver'}
    assert 'gone' not in {u.username for cap in cells.values() for u, _k in cap}
    # split is a manager (⊃ pam_verifier) and sees A's sounds: no PAM gap.
    assert rows[0]['gaps'] == []

    admin = make_user(username='adm', roles=('admin',))
    by_id = {r['institution'].id: r for r in S.matrix(admin)}
    assert set(by_id) == {i.id for i in parks}
    assert by_id[b.id]['gaps'] == ['manage']      # split only sees B


def test_institution_access_page(client_as, db_session, split, parks, make_user):
    resp = client_as(split).get('/uk/admin/institution-access')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Парк А' in html and 'Парк Б' not in html

    verifier = make_user(username='ver2', roles=('ct_verifier',))
    assert client_as(verifier).get('/uk/admin/institution-access').status_code in (302, 403)

    admin = make_user(username='adm3', roles=('admin',))
    html = client_as(admin).get('/uk/admin/institution-access').get_data(as_text=True)
    assert 'Парк Б' in html and 'ia-gap' in html
