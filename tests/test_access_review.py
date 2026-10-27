# SPDX-License-Identifier: AGPL-3.0-only
"""Access review (/admin/access-review, app/admin/access_review.py).

The verified-unit counts come from ct_db / pam_db; here they are injected, so
the tests cover the rule, the apply step and the page against the real main-DB
schema (SQLite).
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

NOW = datetime(2026, 10, 27)
OLD = NOW - timedelta(days=400)
NEW = NOW - timedelta(days=30)


@pytest.fixture
def parks(db_session):
    from app.models import Institution
    a = Institution(name_uk='Парк А', code='PA')
    b = Institution(name_uk='Парк Б', code='PB')
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


@pytest.fixture
def person(db_session, make_user):
    from app.models import UserInstitution

    def _make(username, created, links, roles=('ct_verifier', 'analyst')):
        u = make_user(username=username, roles=roles)
        u.created_at = created
        for inst, flags in links:
            u.institution_links.append(UserInstitution(institution_id=inst.id, **flags))
        db_session.commit()
        return u
    return _make


FULL = dict(can_view_ct=True, can_export_ct=True, can_view_pam=True, can_export_pam=True)


def _pairs(result):
    return {(i['username'], i['institution']) for i in result['items']}


def test_rule_counts_per_institution(db_session, parks, person):
    from app.admin import access_review as ar
    a, b = parks
    u = person('lazy', OLD, [(a, FULL), (b, FULL)])
    counts = {(u.id, a.id): 60, (u.id, b.id): 10}
    res = ar.propose('ct', 'export', now=NOW, counts=counts)
    assert _pairs(res) == {('lazy', 'Парк Б')}
    assert [i['institution'] for i in res['skipped']['enough']] == ['Парк А']


def test_young_excluded_and_admin_are_left_alone(db_session, parks, person):
    from app.admin import access_review as ar
    a, _ = parks
    person('newbie', NEW, [(a, FULL)])
    person('keepme', OLD, [(a, FULL)])
    person('boss', OLD, [(a, FULL)], roles=('admin',))
    res = ar.propose('ct', 'export', exclude=['keepme'], now=NOW, counts={})
    assert res['items'] == []
    assert [i['username'] for i in res['skipped']['too_young']] == ['newbie']
    assert [i['username'] for i in res['skipped']['excluded']] == ['keepme']
    assert [i['username'] for i in res['skipped']['admin']] == ['boss']


def test_months_and_threshold_are_parameters(db_session, parks, person):
    from app.admin import access_review as ar
    a, _ = parks
    u = person('mid', NOW - timedelta(days=100), [(a, FULL)])
    counts = {(u.id, a.id): 20}
    assert ar.propose('ct', 'export', now=NOW, counts=counts)['items'] == []      # 6 months
    assert _pairs(ar.propose('ct', 'export', months=3, now=NOW, counts=counts)) == {('mid', 'Парк А')}
    assert ar.propose('ct', 'export', months=3, min_units=20, now=NOW, counts=counts)['items'] == []


def test_modules_are_independent(db_session, parks, person):
    from app.admin import access_review as ar
    a, _ = parks
    u = person('sounds', OLD, [(a, FULL)])
    # 60 photo series, no sounds verified
    ct = ar.propose('ct', 'export', now=NOW, counts={(u.id, a.id): 60})
    pam = ar.propose('pam', 'export', now=NOW, counts={})
    assert ct['items'] == [] and _pairs(pam) == {('sounds', 'Парк А')}


@pytest.mark.parametrize('module,right', [('ct', 'access'), ('ct', 'export'),
                                          ('pam', 'access'), ('pam', 'export')])
def test_a_parks_manager_keeps_every_right_there(db_session, parks, person, module, right):
    """Managing the park in either module protects all rights in that park only."""
    from app.admin import access_review as ar
    a, b = parks
    person('mgr', OLD, [(a, dict(FULL, can_manage_ct=True)), (b, FULL)], roles=('manager',))
    res = ar.propose(module, right, now=NOW, counts={})
    assert _pairs(res) == {('mgr', 'Парк Б')}
    assert [i['institution'] for i in res['skipped']['manages']] == ['Парк А']


def _flags(user, inst):
    from app.models import UserInstitution
    link = UserInstitution.query.filter_by(user_id=user.id, institution_id=inst.id).first()
    return None if link is None else link.module_flags(user)


def test_apply_export_keeps_view(db_session, parks, person):
    from app.admin import access_review as ar
    a, _ = parks
    u = person('lazy', OLD, [(a, FULL)])
    changed = ar.apply('pam', 'export', [(u.id, a.id)], counts={}, now=NOW)
    db_session.commit()
    assert len(changed) == 1
    f = _flags(u, a)
    assert f['view_pam'] and not f['export_pam'] and f['export_ct']


def test_apply_access_drops_export_and_manage_and_empty_rows(db_session, parks, person):
    from app.admin import access_review as ar
    a, b = parks
    u = person('gone', OLD, [(a, FULL), (b, dict(can_view_ct=True, can_export_ct=True))])
    ar.apply('ct', 'access', [(u.id, a.id), (u.id, b.id)], counts={}, now=NOW)
    db_session.commit()
    f = _flags(u, a)
    assert not f['view_ct'] and not f['export_ct'] and f['view_pam'] and f['export_pam']
    assert _flags(u, b) is None          # nothing left: row removed


def test_apply_ignores_pairs_the_rule_does_not_cover(db_session, parks, person):
    from app.admin import access_review as ar
    a, b = parks
    busy = person('busy', OLD, [(a, FULL)])
    young = person('young', NEW, [(b, FULL)])
    changed = ar.apply('ct', 'export', [(busy.id, a.id), (young.id, b.id), (999, a.id)],
                           counts={(busy.id, a.id): 80}, now=NOW)
    db_session.commit()
    assert changed == []
    assert _flags(busy, a)['export_ct'] and _flags(young, b)['export_ct']


# ── grant ───────────────────────────────────────────────────────────────────

def test_grant_export_to_whoever_verified_enough(db_session, parks, person):
    from app.admin import access_review as ar
    a, b = parks
    view_only = dict(can_view_ct=True, can_view_pam=True)
    worker = person('worker', NEW, [(a, view_only)])          # age does not matter
    idle = person('idle', OLD, [(b, view_only)])
    no_role = person('norole', OLD, [(a, view_only)], roles=('ct_verifier',))
    counts = {(worker.id, a.id): 70, (idle.id, b.id): 3, (no_role.id, a.id): 90}
    res = ar.propose('ct', 'export', direction='grant', now=NOW, counts=counts)
    assert _pairs(res) == {('worker', 'Парк А'), ('norole', 'Парк А')}
    notes = {i['username']: i['note'] for i in res['items']}
    assert notes == {'worker': None, 'norole': 'no_export_role'}
    assert [i['username'] for i in res['skipped']['too_few']] == ['idle']


def test_grant_export_needs_access_first(db_session, parks, person):
    from app.admin import access_review as ar
    a, _ = parks
    outsider = person('outsider', OLD, [])
    res = ar.propose('ct', 'export', direction='grant', now=NOW,
                     counts={(outsider.id, a.id): 500})
    assert res['items'] == []
    assert [i['username'] for i in res['skipped']['no_access']] == ['outsider']


def test_grant_access_to_a_public_volunteer_and_apply(db_session, parks, person):
    from app.admin import access_review as ar
    a, b = parks
    vol = person('volunteer', OLD, [(b, dict(can_view_pam=True))])
    counts = {(vol.id, a.id): 60, (vol.id, b.id): 60}
    res = ar.propose('ct', 'access', direction='grant', now=NOW, counts=counts)
    assert _pairs(res) == {('volunteer', 'Парк А'), ('volunteer', 'Парк Б')}
    ar.apply('ct', 'access', [(vol.id, a.id), (vol.id, b.id)], counts=counts,
             direction='grant')
    db_session.commit()
    fa, fb = _flags(vol, a), _flags(vol, b)
    assert fa['view_ct'] and not fa['export_ct'] and not fa['view_pam']   # new row
    assert fb['view_ct'] and fb['view_pam']                                # existing row


# ── the page ────────────────────────────────────────────────────────────────

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


def test_page_is_admin_only(client_as, make_user):
    mgr = make_user(username='m', roles=('manager',))
    assert client_as(mgr).get('/uk/admin/access-review').status_code in (302, 403)
    adm = make_user(username='adm', roles=('admin',))
    resp = client_as(adm).get('/uk/admin/access-review')
    assert resp.status_code == 200
    assert 'name="months"' in resp.get_data(as_text=True)


def test_analyse_then_apply_through_the_page(client_as, db_session, parks, person, make_user):
    a, _ = parks
    u = person('lazy', OLD, [(a, FULL)])
    adm = make_user(username='adm', roles=('admin',))
    cl = client_as(adm)
    with patch('app.admin.access_review.verified_units', return_value={}):
        html = cl.get('/uk/admin/access-review?analyze=1&module=ct&right=export'
                      '&months=6&min_units=50&direction=revoke').get_data(as_text=True)
        assert f'value="{u.id}:{a.id}"' in html
        resp = cl.post('/uk/admin/access-review/apply', data={
            'direction': 'revoke', 'module': 'ct', 'right': 'export',
            'months': '6', 'min_units': '50',
            'exclude': '', 'pair': [f'{u.id}:{a.id}']})
    assert resp.status_code == 302
    assert not _flags(u, a)['export_ct']


def test_institution_access_page_links_to_the_review(client_as, make_user):
    adm = make_user(username='adm', roles=('admin',))
    html = client_as(adm).get('/uk/admin/institution-access').get_data(as_text=True)
    assert '/admin/access-review' in html


def test_page_grant_lists_people_with_person_checkboxes(client_as, db_session, parks,
                                                         person, make_user):
    a, b = parks
    w = person('worker', OLD, [(a, dict(can_view_ct=True)), (b, dict(can_view_ct=True))])
    adm = make_user(username='adm', roles=('admin',))
    with patch('app.admin.access_review.verified_units',
               return_value={(w.id, a.id): 80, (w.id, b.id): 80}):
        html = client_as(adm).get('/uk/admin/access-review?analyze=1&direction=grant'
                                  '&module=ct&right=export').get_data(as_text=True)
    assert html.count('class="ar-person-cb"') == 1
    assert html.count('name="pair"') == 2


def test_user_list_links_to_the_review_for_admins_only(client_as, make_user):
    adm = make_user(username='adm', roles=('admin',))
    assert '/admin/access-review' in client_as(adm).get('/uk/admin/users').get_data(as_text=True)
    mgr = make_user(username='mgr', roles=('manager',))
    assert '/admin/access-review' not in client_as(mgr).get('/uk/admin/users').get_data(as_text=True)


def test_user_list_shows_a_no_access_chip(client_as, db_session, parks, person, make_user):
    a, b = parks
    person('half', OLD, [(a, FULL)])
    adm = make_user(username='adm', roles=('admin',))
    html = client_as(adm).get('/uk/admin/users').get_data(as_text=True)
    row = html[html.index('data-search="half'):]
    row = row[:row.index('</tr>')]
    assert 'inst-badge-none' in row
    pop = row[row.index('inst-none-pop'):]
    assert 'Парк Б' in pop and 'Парк А' not in pop
