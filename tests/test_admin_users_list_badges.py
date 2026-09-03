"""The user list shows what each institution grant actually covers.

Access is per module since 02-03.09.2026, so "linked to a park" no longer says
what a person may do there. One badge per park carries it:

  * green  — both modules open;
  * blue   — camera traps only;
  * orange — PAM only;
  * a trailing arrow — export, naming the module when only one of two exports.

Institution names (and their order) follow the page language, and the filter bar
can ask for a module.

Run:
    venv/Scripts/python -m pytest tests/test_admin_users_list_badges.py -v
"""
import re

import pytest
from flask import g


@pytest.fixture
def parks(db_session):
    from app.models import Institution

    a = Institution(name_uk='НПП Перший', name_en='Alpha NNP', code='ONE')
    b = Institution(name_uk='НПП Другий', name_en='Beta NNP', code='TWO')
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
    """The <tr> of one user, so assertions cannot pick up a neighbour's badges."""
    match = re.search(rf'<tr[^>]*data-search="{username}[^"]*".*?</tr>', body, re.S)
    assert match, f'no row for {username}'
    return match.group(0)


def _badges(row):
    """(kind, text) per institution badge in a row."""
    return [(m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip())
            for m in re.finditer(r'class="inst-badge inst-badge-(both|ct|pam)"[^>]*>(.*?)</span>\s*(?:</span>)?',
                                 row, re.S)]


# ── the colour carries the module set ──────────────────────────────────────

def test_camera_traps_only_is_blue(app, db_session, make_user, parks, admin_client):
    park, _ = parks
    user = make_user(username='photo_person', roles=('ct_verifier',))
    _grant(db_session, user, park, can_view_ct=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'photo_person')
    assert _badges(row) == [('ct', 'НПП Перший')]


def test_pam_only_is_orange(app, db_session, make_user, parks, admin_client):
    park, _ = parks
    user = make_user(username='sound_person', roles=('pam_verifier',))
    _grant(db_session, user, park, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'sound_person')
    assert _badges(row) == [('pam', 'НПП Перший')]


def test_both_modules_are_green(app, db_session, make_user, parks, admin_client):
    park, _ = parks
    user = make_user(username='both_person', roles=('ct_verifier', 'pam_verifier'))
    _grant(db_session, user, park, can_view_ct=True, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'both_person')
    assert _badges(row) == [('both', 'НПП Перший')]


def test_one_badge_per_park(app, db_session, make_user, parks, admin_client):
    """Compactness is the point: two parks, two badges, whatever the modules."""
    ct_park, pam_park = parks
    user = make_user(username='split_person', roles=('ct_verifier', 'pam_verifier'))
    _grant(db_session, user, ct_park, can_view_ct=True)
    _grant(db_session, user, pam_park, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'split_person')
    assert sorted(_badges(row)) == [('ct', 'НПП Перший'), ('pam', 'НПП Другий')]


# ── the arrow carries export ───────────────────────────────────────────────

def test_no_export_no_arrow(app, db_session, make_user, parks, admin_client):
    park, _ = parks
    user = make_user(username='no_export', roles=('ct_verifier',))
    _grant(db_session, user, park, can_view_ct=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'no_export')
    assert 'inst-exp' not in row


def test_export_everywhere_is_a_bare_arrow(app, db_session, make_user, parks,
                                           admin_client):
    park, _ = parks
    user = make_user(username='full_export', roles=('analyst', 'pam_verifier'))
    _grant(db_session, user, park, can_view_ct=True, can_export_ct=True,
           can_view_pam=True, can_export_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'full_export')
    arrow = re.search(r'<span class="inst-exp">(.*?)</span>', row, re.S).group(1)
    assert arrow.strip() == '↓', 'both modules export, so the arrow needs no label'


def test_partial_export_names_its_module(app, db_session, make_user, parks,
                                         admin_client):
    """Both modules open but only sounds export: the arrow must say so, or the
    badge would promise a camera-trap export nobody has."""
    park, _ = parks
    user = make_user(username='half_export', roles=('analyst', 'pam_verifier'))
    _grant(db_session, user, park, can_view_ct=True, can_view_pam=True,
           can_export_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'half_export')
    arrow = re.search(r'<span class="inst-exp">(.*?)</span>', row, re.S).group(1)
    assert 'ПАМ' in arrow
    assert 'ФП' not in arrow


def test_single_module_export_is_a_bare_arrow(app, db_session, make_user, parks,
                                              admin_client):
    """Only sounds are open and they export: the colour already says which
    module, so the arrow stays bare."""
    park, _ = parks
    user = make_user(username='pam_export', roles=('analyst', 'pam_verifier'))
    _grant(db_session, user, park, can_view_pam=True, can_export_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'pam_export')
    arrow = re.search(r'<span class="inst-exp">(.*?)</span>', row, re.S).group(1)
    assert arrow.strip() == '↓'


# ── language ───────────────────────────────────────────────────────────────

def test_badges_follow_the_page_language(app, db_session, make_user, parks,
                                         admin_client):
    park, _ = parks
    user = make_user(username='lang_person', roles=('ct_verifier',))
    _grant(db_session, user, park, can_view_ct=True)

    uk_row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'lang_person')
    assert _badges(uk_row) == [('ct', 'НПП Перший')]

    en_row = _row(admin_client.get('/en/admin/users').get_data(as_text=True), 'lang_person')
    assert _badges(en_row) == [('ct', 'Alpha NNP')]


def test_the_filter_dropdown_follows_the_page_language(app, db_session, make_user,
                                                       parks, admin_client):
    park, _ = parks
    user = make_user(username='filter_lang', roles=('ct_verifier',))
    _grant(db_session, user, park, can_view_ct=True)

    body = admin_client.get('/en/admin/users').get_data(as_text=True)
    options = re.search(r'id="filter-institution".*?</select>', body, re.S).group(0)
    assert 'Alpha NNP' in options
    assert 'НПП Перший' not in options


def test_an_institution_without_an_english_name_falls_back(app, db_session,
                                                           make_user, admin_client):
    from app.models import Institution

    park = Institution(name_uk='НПП Без Перекладу', code='NOEN')
    db_session.add(park)
    db_session.commit()
    user = make_user(username='fallback_person', roles=('ct_verifier',))
    _grant(db_session, user, park, can_view_ct=True)

    row = _row(admin_client.get('/en/admin/users').get_data(as_text=True), 'fallback_person')
    assert _badges(row) == [('ct', 'НПП Без Перекладу')]


def test_badges_are_ordered_by_the_displayed_name(app, db_session, make_user,
                                                  parks, admin_client):
    """Sorting by the Ukrainian name while showing English reads as random, so
    the order must follow whatever is on screen."""
    a, b = parks                       # НПП Перший / Alpha NNP, НПП Другий / Beta NNP
    user = make_user(username='order_person', roles=('ct_verifier',))
    _grant(db_session, user, a, can_view_ct=True)
    _grant(db_session, user, b, can_view_ct=True)

    uk_names = [text for _, text in _badges(
        _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'order_person'))]
    en_names = [text for _, text in _badges(
        _row(admin_client.get('/en/admin/users').get_data(as_text=True), 'order_person'))]

    assert uk_names == ['НПП Другий', 'НПП Перший']
    assert en_names == ['Alpha NNP', 'Beta NNP']


# ── the rest of the cell ───────────────────────────────────────────────────

def test_a_user_without_institutions_renders_the_placeholder(app, db_session,
                                                             make_user, parks,
                                                             admin_client):
    make_user(username='no_parks', roles=('viewer',))
    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'no_parks')
    assert 'inst-badge' not in row
    assert '---' in row


def test_the_row_carries_a_per_module_institution_list(app, db_session, make_user,
                                                       parks, admin_client):
    """The filter reads these attributes; a park granted for sounds only must
    not appear in the camera-trap list."""
    ct_park, pam_park = parks
    user = make_user(username='attrs_person', roles=('ct_verifier', 'pam_verifier'))
    _grant(db_session, user, ct_park, can_view_ct=True)
    _grant(db_session, user, pam_park, can_view_pam=True)

    row = _row(admin_client.get('/uk/admin/users').get_data(as_text=True), 'attrs_person')
    assert f'data-inst-ct="{ct_park.id}"' in row
    assert f'data-inst-pam="{pam_park.id}"' in row


def test_the_page_explains_the_colours_and_offers_the_module_filter(
        app, db_session, make_user, parks, admin_client):
    body = admin_client.get('/uk/admin/users').get_data(as_text=True)
    assert 'inst-legend' in body, 'a colour nobody can decode is noise'
    for kind in ('both', 'ct', 'pam'):
        assert f'inst-badge-{kind}' in body, kind
    assert 'id="filter-module"' in body
    assert 'value="pam"' in body


def test_the_legend_keeps_the_export_example_on_its_own_row(
        app, db_session, make_user, parks, admin_client):
    """On one line the arrow sample reads as a fourth colour, so the colours and
    the export rule are two rows."""
    body = admin_client.get('/uk/admin/users').get_data(as_text=True)
    # The rows hold spans only, so a non-greedy match ends at the row's own tag.
    rows = re.findall(r'<div class="inst-legend-row">(.*?)</div>', body, re.S)
    assert len(rows) == 2, 'colours in one row, the export example in another'

    colours_row, export_row = rows
    assert colours_row.count('inst-badge-') == 3, 'three colour samples'
    assert 'inst-exp' not in colours_row, 'the arrow belongs to the second row'
    assert 'inst-exp' in export_row
    assert export_row.count('inst-badge-') == 1
