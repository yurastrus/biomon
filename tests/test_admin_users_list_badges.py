"""The user list shows what each institution grant actually covers.

Access is per module since 02-03.09.2026, so "linked to a park" no longer says
what a person may do there. The list renders one chip per granted module next to
each park (with an arrow when export is granted too), and the filter bar can ask
for a module.

Run:
    venv/Scripts/python -m pytest tests/test_admin_users_list_badges.py -v
"""
import re

import pytest
from flask import g


@pytest.fixture
def parks(db_session):
    from app.models import Institution

    a = Institution(name_uk='НПП Перший', name_en='First NNP', code='ONE')
    b = Institution(name_uk='НПП Другий', name_en='Second NNP', code='TWO')
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


@pytest.fixture
def admin_client(app, db_session, make_user):
    admin = make_user(username='root_list', roles=('admin',))
    g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(admin.id)
        sess['_fresh'] = True
    return client


def _grant(db_session, user, park, **flags):
    from app.models import UserInstitution

    user.institution_links.append(UserInstitution(institution_id=park.id, **flags))
    db_session.commit()


def _row(body, username):
    """The <tr> of one user, so assertions cannot pick up a neighbour's chips."""
    match = re.search(rf'<tr[^>]*data-search="{username}[^"]*".*?</tr>', body, re.S)
    assert match, f'no row for {username}'
    return match.group(0)


def test_a_camera_trap_only_grant_shows_one_chip(app, db_session, make_user,
                                                 parks, admin_client):
    ct_park, _ = parks
    user = make_user(username='photo_person', roles=('ct_verifier',))
    _grant(db_session, user, ct_park, can_view_ct=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'photo_person')
    assert 'НПП Перший' in row
    assert 'mod-chip-ct' in row
    assert 'mod-chip-pam' not in row, 'nothing was granted for sounds'
    assert 'mod-exp' not in row, 'no export was granted'


def test_a_pam_only_grant_shows_the_other_chip(app, db_session, make_user,
                                               parks, admin_client):
    _, pam_park = parks
    user = make_user(username='sound_person', roles=('pam_verifier',))
    _grant(db_session, user, pam_park, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'sound_person')
    assert 'mod-chip-pam' in row
    assert 'mod-chip-ct' not in row


def test_export_is_marked_on_its_own_module(app, db_session, make_user,
                                            parks, admin_client):
    """Export on sounds only must not decorate the camera-trap chip."""
    ct_park, _ = parks
    user = make_user(username='exporter_person', roles=('analyst', 'pam_verifier'))
    _grant(db_session, user, ct_park, can_view_ct=True, can_view_pam=True,
           can_export_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True),
               'exporter_person')
    ct_chip = re.search(r'<span class="mod-chip mod-chip-ct".*?</span>\s*(?:</span>)?', row, re.S).group(0)
    pam_chip = re.search(r'<span class="mod-chip mod-chip-pam".*?</span>\s*(?:</span>)?', row, re.S).group(0)
    assert 'mod-exp' not in ct_chip
    assert 'mod-exp' in pam_chip


def test_both_modules_show_both_chips(app, db_session, make_user, parks, admin_client):
    ct_park, _ = parks
    user = make_user(username='both_person', roles=('ct_verifier', 'pam_verifier'))
    _grant(db_session, user, ct_park, can_view_ct=True, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'both_person')
    assert 'mod-chip-ct' in row and 'mod-chip-pam' in row


def test_the_row_carries_a_per_module_institution_list(app, db_session, make_user,
                                                       parks, admin_client):
    """The filter reads these attributes; a park granted for sounds only must
    not appear in the camera-trap list."""
    ct_park, pam_park = parks
    user = make_user(username='split_person', roles=('ct_verifier', 'pam_verifier'))
    _grant(db_session, user, ct_park, can_view_ct=True)
    _grant(db_session, user, pam_park, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'split_person')
    assert f'data-inst-ct="{ct_park.id}"' in row
    assert f'data-inst-pam="{pam_park.id}"' in row


def test_a_user_without_institutions_renders_the_placeholder(app, db_session,
                                                             make_user, parks,
                                                             admin_client):
    make_user(username='no_parks', roles=('viewer',))
    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'no_parks')
    assert 'mod-chip' not in row
    assert '---' in row


def test_the_page_explains_the_chips_and_offers_the_module_filter(app, db_session,
                                                                  make_user, parks,
                                                                  admin_client):
    body = admin_client.get('/uk/admin/users').get_data(as_text=True)
    assert 'inst-legend' in body, 'a chip nobody can decode is noise'
    assert 'id="filter-module"' in body
    assert 'value="pam"' in body
