"""The admin user form writes the four per-module grants.

One row per institution, four checkboxes: see and export camera-trap data, see
and export PAM data. The form is the only place a person sets these by hand, so
its POST shape, its re-render and the writer's invariants are pinned here.

Since phase 4 (03.09.2026) the flags are the only truth: the legacy `can_export`
column is gone, and a row exists only while it grants something.

Run:
    venv/Scripts/python -m pytest tests/test_module_access_form.py -v
"""
import pytest


@pytest.fixture
def park(db_session):
    from app.models import Institution

    inst = Institution(name_uk='НПП Ужанський', name_en='Uzhanskyi NNP', code='UZH')
    db_session.add(inst)
    db_session.commit()
    return inst


@pytest.fixture
def linked_user(db_session, make_user, park):
    """Factory: a user with one institution row and the given grants."""
    from app.models import UserInstitution

    def _make(username, roles=('viewer',), **flags):
        user = make_user(username=username, roles=roles)
        link = UserInstitution(institution_id=park.id, **flags)
        user.institution_links.append(link)
        db_session.commit()
        return user, link
    return _make


# ── phase 2: the admin form writes the four grants ─────────────────────────
# The form is the only place these columns are set by hand, so its POST shape
# and its re-render are pinned here together with the writer's invariants.

@pytest.fixture
def admin_client(app, db_session, make_user):
    from flask import g

    def _login(user):
        g.pop('_login_user', None)
        cl = app.test_client()
        with cl.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
        return cl

    admin = make_user(username='root_admin', roles=('admin',))
    return _login(admin)


def _edit(client, user, park, **fields):
    """POST the user form the way the page does.

    The rendered form pre-checks the roles the person already holds, so the
    submission carries them; without that the save would look like a role
    removal and the export columns would be preserved instead of rewritten.
    """
    data = {'username': user.username, 'email': user.email or '',
            'first_name': '', 'last_name': '',
            'roles': [str(r.id) for r in user.roles]}
    data.update(fields)
    return client.post(f'/uk/admin/users/edit/{user.id}', data=data)


def test_form_writes_pam_only_access(db_session, linked_user, admin_client, park):
    from app.models import UserInstitution

    user, _ = linked_user('subject', roles=('ct_verifier', 'pam_verifier'))
    resp = _edit(admin_client, user, park, view_pam=[str(park.id)])
    assert resp.status_code in (200, 302)

    db_session.expire_all()
    link = db_session.query(UserInstitution).filter_by(user_id=user.id).one()
    assert (link.can_view_ct, link.can_view_pam) == (False, True)


def test_form_removes_the_row_when_nothing_is_ticked(db_session, linked_user,
                                                     admin_client, park):
    from app.models import UserInstitution

    user, _ = linked_user('subject2', roles=('ct_verifier',))
    _edit(admin_client, user, park)

    db_session.expire_all()
    assert db_session.query(UserInstitution).filter_by(user_id=user.id).count() == 0, \
        'a park with no grant leaves no row'


def test_export_is_written_per_module(db_session, linked_user, admin_client, park):
    """Export ticked for sounds only must not open the camera-trap export."""
    from app.models import UserInstitution

    user, _ = linked_user('exporter', roles=('analyst', 'pam_verifier'))
    _edit(admin_client, user, park,
          view_pam=[str(park.id)], export_pam=[str(park.id)])

    db_session.expire_all()
    link = db_session.query(UserInstitution).filter_by(user_id=user.id).one()
    assert link.can_export_pam is True
    assert link.can_export_ct is False
    assert [i.code for i in user.export_institutions_for('pam')] == ['UZH']
    assert user.export_institutions_for('ct') == []


def test_form_drops_export_without_access_in_the_same_module(db_session, linked_user,
                                                             admin_client, park):
    from app.models import UserInstitution

    user, _ = linked_user('crafted', roles=('analyst', 'pam_verifier'))
    _edit(admin_client, user, park,
          view_ct=[str(park.id)], export_pam=[str(park.id)])

    db_session.expire_all()
    link = db_session.query(UserInstitution).filter_by(user_id=user.id).one()
    assert link.can_view_pam is False
    assert link.can_export_pam is False, 'export needs access in its own module'


def test_the_form_shows_four_columns_and_seeds_them(db_session, linked_user,
                                                    admin_client, park):
    user, link = linked_user('rendered', roles=('pam_verifier',),
                             can_view_ct=True, can_export_ct=False,
                             can_view_pam=True, can_export_pam=False)
    body = admin_client.get(f'/uk/admin/users/edit/{user.id}').get_data(as_text=True)

    for field in ('view_ct', 'export_ct', 'view_pam', 'export_pam'):
        assert f'name="{field}"' in body, field
    assert f'id="view_ct_{park.id}"' in body
    # both access boxes seeded from the stored flags
    assert body.count('checked') >= 2


def test_the_form_renders_every_granted_flag_as_checked(db_session, linked_user,
                                                        admin_client, park):
    """A save must be able to round-trip: what is granted comes back ticked, or
    the next save would silently clear it."""
    import re

    user, _ = linked_user('all_four', roles=('analyst', 'pam_verifier'),
                          can_view_ct=True, can_export_ct=True,
                          can_view_pam=True, can_export_pam=True)

    body = admin_client.get(f'/uk/admin/users/edit/{user.id}').get_data(as_text=True)
    for field in ('view_ct', 'view_pam', 'export_ct', 'export_pam'):
        pattern = rf'name="{field}" value="{park.id}"[^>]*checked'
        assert re.search(pattern, body), f'{field} should render as granted'
