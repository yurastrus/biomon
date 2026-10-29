# SPDX-License-Identifier: AGPL-3.0-only
"""Bulk verifier editing on /admin/institution-access (the "+" / "-" popups).

* "+" grants the module's access to one institution and, when missing, the
  verifier role. "-" takes the institution's module away and keeps the role.
* A manager acts only where they manage that module, may add anyone, and may
  revoke only people ranked below them; nobody revokes themselves.
* Revoking never touches the person's other institutions or other module.
"""
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


def _link(inst_id, ct=True, pam=False, manage_ct=False, export_ct=False):
    from app.models import UserInstitution
    return UserInstitution(institution_id=inst_id, can_view_ct=ct, can_view_pam=pam,
                           can_export_ct=export_ct, can_export_pam=False,
                           can_manage_ct=manage_ct, can_manage_pam=False)


@pytest.fixture
def manager(db_session, make_user, parks):
    """Manages park A for photos only; sees park B."""
    a, b = parks
    u = make_user(username='mgr', roles=('manager',))
    u.institution_links.extend([_link(a.id, manage_ct=True), _link(b.id)])
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


def _url(inst, module='ct'):
    return f'/uk/admin/institution-access/{inst.id}/verifiers/{module}'


def _link_of(user, inst):
    return next((l for l in user.institution_links if l.institution_id == inst.id), None)


# ── the service ─────────────────────────────────────────────────────────────

def test_grant_gives_access_and_missing_role(db_session, make_user, parks, manager):
    from app.admin.services import InstitutionVerifierService as S
    a, _ = parks
    newbie = make_user(username='newbie', roles=('viewer',))

    result = S.grant(manager, a.id, 'ct', [newbie.id])
    db_session.commit()

    assert result['granted'] == [newbie]
    assert result['role_granted'] == [newbie]
    assert newbie.has_role('ct_verifier')
    link = _link_of(newbie, a)
    assert link.can_view_ct and not link.can_view_pam and not link.can_export_ct
    assert S.verifies(newbie, a.id, 'ct')


def test_grant_keeps_existing_row_and_does_not_regrant_role(db_session, make_user, parks, manager):
    from app.admin.services import InstitutionVerifierService as S
    a, _ = parks
    ver = make_user(username='ver', roles=('ct_verifier',))
    ver.institution_links.append(_link(a.id, ct=False, pam=True))
    db_session.commit()

    result = S.grant(manager, a.id, 'ct', [ver.id])
    db_session.commit()
    assert result['role_granted'] == []
    link = _link_of(ver, a)
    assert link.can_view_ct and link.can_view_pam


def test_grant_answers_only_this_parks_pending_request(db_session, make_user, parks, manager):
    from app.admin.services import InstitutionVerifierService as S
    from app.models import VerificationRequest, VerificationRequestInstitution
    a, b = parks
    applicant = make_user(username='applicant')
    req = VerificationRequest(user_id=applicant.id, module='ct')
    db_session.add(req)
    db_session.flush()
    db_session.add_all([VerificationRequestInstitution(request_id=req.id, institution_id=a.id),
                        VerificationRequestInstitution(request_id=req.id, institution_id=b.id)])
    db_session.commit()

    S.grant(manager, a.id, 'ct', [applicant.id])
    db_session.commit()

    statuses = {r.institution_id: r.status for r in req.institution_rows()}
    assert statuses[a.id] == VerificationRequest.STATUS_APPROVED
    assert statuses[b.id] == VerificationRequest.STATUS_PENDING
    assert req.status == VerificationRequest.STATUS_PENDING   # park B still to answer


def test_revoke_keeps_role_other_module_and_other_parks(db_session, make_user, parks, manager):
    from app.admin.services import InstitutionVerifierService as S
    a, b = parks
    ver = make_user(username='ver', roles=('ct_verifier', 'analyst'))
    ver.institution_links.extend([_link(a.id, pam=True, export_ct=True), _link(b.id)])
    db_session.commit()

    assert S.removal_note(ver, a.id, 'ct') == ['export']
    result = S.revoke(manager, a.id, 'ct', [ver.id])
    db_session.commit()

    assert result['revoked'] == [ver]
    assert ver.has_role('ct_verifier')
    link = _link_of(ver, a)
    assert not link.can_view_ct and not link.can_export_ct and link.can_view_pam
    assert _link_of(ver, b).can_view_ct


def test_revoke_drops_an_empty_row(db_session, make_user, parks, manager):
    from app.admin.services import InstitutionVerifierService as S
    from app.models import UserInstitution
    a, _ = parks
    ver = make_user(username='ver', roles=('ct_verifier',))
    ver.institution_links.append(_link(a.id))
    db_session.commit()

    S.revoke(manager, a.id, 'ct', [ver.id])
    db_session.commit()
    assert UserInstitution.query.filter_by(user_id=ver.id).count() == 0


def test_manager_cannot_revoke_peers_admins_or_self(db_session, make_user, parks, manager):
    from app.admin.services import InstitutionVerifierService as S
    a, _ = parks
    peer = make_user(username='peer', roles=('manager',))
    admin = make_user(username='adm', roles=('admin',))
    for u in (peer, admin):
        u.institution_links.append(_link(a.id))
    db_session.commit()

    result = S.revoke(manager, a.id, 'ct', [peer.id, admin.id, manager.id])
    db_session.commit()
    assert result['revoked'] == []
    assert {u.username for u in result['skipped']} == {'peer', 'adm', 'mgr'}
    assert _link_of(peer, a).can_view_ct and _link_of(manager, a).can_view_ct

    # An admin may revoke a manager, but not themselves.
    result = S.revoke(admin, a.id, 'ct', [peer.id, admin.id])
    db_session.commit()
    assert [u.username for u in result['revoked']] == ['peer']


def test_lists_split_people_by_whether_they_verify(db_session, make_user, parks):
    from app.admin.services import InstitutionVerifierService as S
    a, _ = parks
    ver = make_user(username='ver', roles=('ct_verifier',))
    ver.institution_links.append(_link(a.id))
    sees = make_user(username='sees', roles=('viewer',))       # access, no role
    sees.institution_links.append(_link(a.id))
    other = make_user(username='other', roles=('viewer',))
    make_user(username='adm', roles=('admin',))
    gone = make_user(username='gone', roles=('viewer',))
    gone.is_active = False
    db_session.commit()

    add = {u.username for u in S.candidates_to_add(a.id, 'ct')}
    assert add == {'sees', 'other'}
    assert {u.username for u in S.current_verifiers(a.id, 'ct')} == {'ver'}
    # PAM: nobody verifies sounds there.
    assert 'ver' in {u.username for u in S.candidates_to_add(a.id, 'pam')}


# ── the endpoints ───────────────────────────────────────────────────────────

def test_manager_endpoints_follow_module_and_park(client_as, db_session, make_user, parks, manager):
    a, b = parks
    cl = client_as(manager)
    assert cl.get(_url(a) + '?mode=add').status_code == 200
    assert cl.get(_url(a, 'pam') + '?mode=add').status_code == 403   # photos only
    assert cl.get(_url(b) + '?mode=add').status_code == 403          # sees, not manages
    assert cl.get(_url(a, 'xyz')).status_code == 403

    verifier = make_user(username='v', roles=('ct_verifier',))
    assert client_as(verifier).get(_url(a)).status_code in (302, 403)


def test_add_list_has_no_emails(client_as, db_session, make_user, parks, manager):
    a, _ = parks
    stranger = make_user(username='stranger', roles=('viewer',))
    stranger.email = 'stranger@example.org'
    db_session.commit()
    data = client_as(manager).get(_url(a) + '?mode=add').get_json()
    row = next(u for u in data['users'] if u['username'] == 'stranger')
    assert set(row) == {'id', 'username', 'name', 'locked', 'also'}
    assert 'example.org' not in str(data)


def test_post_add_then_remove(client_as, db_session, make_user, parks, manager):
    a, _ = parks
    stranger = make_user(username='stranger', roles=('viewer',))
    stranger.email = 'stranger@example.org'
    db_session.commit()
    cl = client_as(manager)

    with patch('app.admin.routes.send_rights_granted_email') as mail:
        resp = cl.post(_url(a), json={'mode': 'add', 'user_ids': [stranger.id]})
    assert resp.status_code == 200 and resp.get_json()['changed'] == 1
    mail.assert_called_once()
    db_session.refresh(stranger)
    assert stranger.has_role('ct_verifier') and _link_of(stranger, a).can_view_ct

    rows = cl.get(_url(a) + '?mode=remove').get_json()['users']
    by_name = {u['username']: u for u in rows}
    assert by_name['stranger']['locked'] is False
    assert by_name['mgr']['locked'] is True

    resp = cl.post(_url(a), json={'mode': 'remove', 'user_ids': [stranger.id, manager.id]})
    body = resp.get_json()
    assert body['changed'] == 1 and body['skipped'] == 1
    db_session.refresh(stranger)
    assert _link_of(stranger, a) is None and stranger.has_role('ct_verifier')


def test_post_rejects_bad_payload(client_as, parks, manager):
    a, _ = parks
    cl = client_as(manager)
    assert cl.post(_url(a), json={'mode': 'nope', 'user_ids': []}).status_code == 400
    assert cl.post(_url(a), json={'mode': 'add', 'user_ids': ['x']}).status_code == 400


def test_page_shows_buttons_only_in_editable_cells(client_as, parks, manager, make_user):
    html = client_as(manager).get('/uk/admin/institution-access').get_data(as_text=True)
    assert 'data-module="ct"' in html
    assert 'data-module="pam"' not in html     # manages park A for photos only

    admin = make_user(username='adm', roles=('admin',))
    html = client_as(admin).get('/uk/admin/institution-access').get_data(as_text=True)
    assert 'data-module="pam"' in html
