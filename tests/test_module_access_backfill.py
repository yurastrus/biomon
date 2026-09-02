"""Per-module institution access, phase 1: storage plus the backfill rule.

Nothing here touches the read paths — the site still decides access by the
legacy columns (a `user_institutions` row = access, `can_export` = export). What
is pinned:

  * the translation rule from one legacy row into the four new flags;
  * `UserInstitution.module_flags()`, including the fallback for a NULL column
    ("never decided") so a row written by older code stays readable;
  * the backfill only fills undecided flags, leaving deliberate choices alone,
    and `--recompute` is the only way to overwrite them.

Run:
    venv/Scripts/python -m pytest tests/test_module_access_backfill.py -v
"""
import pytest

from scripts.backfill_module_access import target_flags, plan_row


@pytest.fixture
def park(db_session):
    from app.models import Institution

    inst = Institution(name_uk='НПП Ужанський', name_en='Uzhanskyi NNP', code='UZH')
    db_session.add(inst)
    db_session.commit()
    return inst


@pytest.fixture
def linked_user(db_session, make_user, park):
    """Factory: a user with one institution row, given roles and export flag."""
    from app.models import UserInstitution

    def _make(username, roles=('viewer',), can_export=False, **flags):
        user = make_user(username=username, roles=roles)
        link = UserInstitution(institution_id=park.id, can_export=can_export, **flags)
        user.institution_links.append(link)
        db_session.commit()
        return user, link
    return _make


# ── the rule ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('can_export,may_pam,expected', [
    # camera traps is always on: the row itself was the grant
    (False, False, {'can_view_ct': True, 'can_export_ct': False,
                    'can_view_pam': False, 'can_export_pam': False}),
    # export copies over, but only for the module the person may work in
    (True, False, {'can_view_ct': True, 'can_export_ct': True,
                   'can_view_pam': False, 'can_export_pam': False}),
    # a sound verifier gets PAM access on the parks they already had
    (False, True, {'can_view_ct': True, 'can_export_ct': False,
                   'can_view_pam': True, 'can_export_pam': False}),
    # PAM export needs both PAM access and an export right that already existed
    (True, True, {'can_view_ct': True, 'can_export_ct': True,
                  'can_view_pam': True, 'can_export_pam': True}),
])
def test_target_flags(can_export, may_pam, expected):
    assert target_flags(can_export=can_export, may_verify_pam=may_pam) == expected


def test_nobody_gains_an_export_right_they_did_not_have():
    """The owner's rule, stated as its own test: no export on PAM for a park
    where the person had no export at all."""
    flags = target_flags(can_export=False, may_verify_pam=True)
    assert flags['can_export_pam'] is False
    assert flags['can_view_pam'] is True, 'access yes, export no'


# ── module_flags() and its NULL fallback ───────────────────────────────────

def test_undecided_row_falls_back_to_the_legacy_meaning(db_session, linked_user):
    user, link = linked_user('pam_person', roles=('pam_verifier',), can_export=True)
    flags = link.module_flags(user)
    assert flags == {'view_ct': True, 'export_ct': True,
                     'view_pam': True, 'export_pam': True}


def test_undecided_row_gives_no_pam_to_a_photo_only_verifier(db_session, linked_user):
    user, link = linked_user('only_ct', roles=('ct_verifier',), can_export=True)
    flags = link.module_flags(user)
    assert flags['view_ct'] is True and flags['export_ct'] is True
    assert flags['view_pam'] is False and flags['export_pam'] is False


def test_manager_counts_as_a_sound_verifier(db_session, linked_user):
    """`manager` implies pam_verifier through the role hierarchy, and the
    fallback must respect that rather than matching role names literally."""
    user, link = linked_user('boss', roles=('manager',), can_export=False)
    assert link.module_flags(user)['view_pam'] is True


def test_without_a_user_the_fallback_denies_pam(db_session, linked_user):
    """Callers that cannot say who the row belongs to get the safe answer."""
    _, link = linked_user('someone', roles=('pam_verifier',), can_export=True)
    assert link.module_flags()['view_pam'] is False


def test_decided_flags_win_over_the_fallback(db_session, linked_user):
    user, link = linked_user('decided', roles=('pam_verifier',), can_export=True,
                             can_view_pam=False, can_export_pam=False,
                             can_view_ct=True, can_export_ct=False)
    flags = link.module_flags(user)
    assert flags == {'view_ct': True, 'export_ct': False,
                     'view_pam': False, 'export_pam': False}


# ── the backfill ───────────────────────────────────────────────────────────

def test_plan_fills_an_undecided_row(db_session, linked_user):
    user, link = linked_user('fresh', roles=('pam_verifier',), can_export=True)
    assert plan_row(link, user) == {'can_view_ct': True, 'can_export_ct': True,
                                    'can_view_pam': True, 'can_export_pam': True}


def test_plan_leaves_a_decided_row_alone(db_session, linked_user):
    """A manager unticked PAM in the admin form; a re-run must not undo that."""
    user, link = linked_user('chosen', roles=('pam_verifier',), can_export=True,
                             can_view_ct=True, can_export_ct=True,
                             can_view_pam=False, can_export_pam=False)
    assert plan_row(link, user) == {}


def test_recompute_overwrites_a_decided_row(db_session, linked_user):
    user, link = linked_user('chosen2', roles=('pam_verifier',), can_export=True,
                             can_view_ct=True, can_export_ct=True,
                             can_view_pam=False, can_export_pam=False)
    assert plan_row(link, user, recompute=True) == {'can_view_pam': True,
                                                    'can_export_pam': True}


def test_plan_fills_only_the_columns_that_are_null(db_session, linked_user):
    user, link = linked_user('half', roles=('pam_verifier',), can_export=False,
                             can_view_ct=True)
    assert plan_row(link, user) == {'can_export_ct': False, 'can_view_pam': True,
                                    'can_export_pam': False}


def test_the_run_writes_the_flags_and_is_idempotent(db_session, linked_user, app):
    """End to end against the real session, then a second pass changes nothing."""
    from scripts import backfill_module_access as backfill

    user, link = linked_user('endtoend', roles=('pam_verifier',), can_export=True)

    # run() builds its own app; here the fixtures already own the session, so
    # exercise the loop the same way run() does, over this session's rows.
    from app.models import UserInstitution
    for row in db_session.query(UserInstitution).all():
        for name, value in backfill.plan_row(row, user).items():
            setattr(row, name, value)
    db_session.commit()

    db_session.expire_all()
    row = db_session.query(UserInstitution).one()
    assert (row.can_view_ct, row.can_export_ct,
            row.can_view_pam, row.can_export_pam) == (True, True, True, True)
    assert backfill.plan_row(row, user) == {}, 'second pass is a no-op'


def test_the_legacy_columns_are_untouched(db_session, linked_user):
    """Phase 1 must not change what the live site reads."""
    from app.models import UserInstitution

    user, link = linked_user('legacy', roles=('pam_verifier',), can_export=True)
    for name, value in plan_row(link, user).items():
        setattr(link, name, value)
    db_session.commit()
    db_session.expire_all()

    row = db_session.query(UserInstitution).one()
    assert row.can_export is True, 'the legacy export flag stays as it was'
    assert [i.code for i in user.institutions] == ['UZH'], 'the row still grants access'


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


def test_form_keeps_the_legacy_export_column_in_sync(db_session, linked_user,
                                                     admin_client, park, make_role):
    """The live export paths still read can_export, so it must reflect
    "may export in either module" until phase 3 switches them over."""
    from app.models import UserInstitution

    user, _ = linked_user('exporter', roles=('analyst', 'pam_verifier'))
    _edit(admin_client, user, park,
          view_pam=[str(park.id)], export_pam=[str(park.id)])

    db_session.expire_all()
    link = db_session.query(UserInstitution).filter_by(user_id=user.id).one()
    assert link.can_export_pam is True
    assert link.can_export_ct is False
    assert link.can_export is True, 'legacy column follows either module'


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


def test_a_legacy_row_renders_as_the_access_it_grants(db_session, linked_user,
                                                      admin_client, park):
    """All four columns NULL (written before phase 1): the form must show what
    the row means today, not an empty line the next save would silently clear."""
    user, link = linked_user('legacy_row', roles=('pam_verifier',), can_export=True)
    assert link.can_view_ct is None

    body = admin_client.get(f'/uk/admin/users/edit/{user.id}').get_data(as_text=True)
    import re
    for field in ('view_ct', 'view_pam', 'export_ct', 'export_pam'):
        pattern = rf'name="{field}" value="{park.id}"[^>]*checked'
        assert re.search(pattern, body), f'{field} should render as granted'
