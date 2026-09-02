"""Institutions requested at registration, and per-institution approval.

What is pinned here:
  * the registration form offers the institution list and stores the picked ones
    as *requests* (never as grants);
  * a manager sees only requests naming an institution of theirs, and only those
    institutions inside the request;
  * approving as a manager grants the module role plus that one institution and
    leaves the request pending for the others (the two-parks scenario);
  * an admin can answer everything at once, and unticking an institution removes
    it from the request;
  * a rejection grants nothing and closes only the decider's own institutions.

Emails are asserted at the app.utils.emails.send_email boundary — SMTP is never
touched. Flask-Login caches the user on ``g``; tests that switch identity call
``forget_cached_user()`` (see tests/test_registration.py).

Run:
    venv/Scripts/python -m pytest tests/test_verification_institutions.py -v
"""
from unittest.mock import patch

import pytest
from flask import g


REG_DATA = {
    'first_name': 'Іван',
    'last_name': 'Франко',
    'email': 'ivan@example.com',
    'username': 'ivan',
    'password': 'parol12345',
    'confirm_password': 'parol12345',
    'wants_ct': 'y',
    'consent': 'y',
}


def forget_cached_user():
    g.pop('_login_user', None)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.extensions import limiter
    storage = getattr(limiter, '_storage', None)
    if storage is not None:
        try:
            storage.reset()
        except Exception:
            pass
    yield


@pytest.fixture
def mail_out():
    sent = []

    def _fake(subject, recipients, body):
        sent.append({'subject': subject, 'to': list(recipients), 'body': body})
        return True

    with patch('app.utils.emails.send_email', side_effect=_fake), \
         patch('app.utils.notifications.send_notification'):
        yield sent


@pytest.fixture
def no_admin_email(app):
    """Drop ADMIN_EMAIL for tests that assert the exact recipient list.

    The testing config carries one; production does not (which is exactly the
    case that used to leave the letter with nowhere to go).
    """
    saved = app.config.get('ADMIN_EMAIL')
    app.config['ADMIN_EMAIL'] = None
    try:
        yield
    finally:
        app.config['ADMIN_EMAIL'] = saved


@pytest.fixture
def parks(db_session):
    """Two institutions, as in the Skole Beskids / Uzhanskyi example."""
    from app.models import Institution

    skole = Institution(name_uk='НПП Сколівські Бескиди', name_en='Skole Beskids NNP',
                        code='SKB', ecoregion_uk='Карпати', ecoregion_en='Carpathians')
    uzh = Institution(name_uk='НПП Ужанський', name_en='Uzhanskyi NNP',
                      code='UZH', ecoregion_uk='Карпати', ecoregion_en='Carpathians')
    db_session.add_all([skole, uzh])
    db_session.commit()
    return skole, uzh


@pytest.fixture
def manager_of(db_session, make_user):
    """Factory: a manager with access to the given institutions."""
    from app.models import UserInstitution

    def _make(username, institutions, email=None):
        user = make_user(username=username, roles=('manager',))
        user.email = email
        for inst in institutions:
            user.institution_links.append(
                UserInstitution(institution_id=inst.id, can_export=False))
        db_session.commit()
        return user
    return _make


def _register(client, institution_ids=(), **overrides):
    data = dict(REG_DATA)
    data.update(overrides)
    if institution_ids:
        data['institutions'] = [str(i) for i in institution_ids]
    return client.post('/uk/register', data=data)


def _confirm_link_path(mail):
    link = next(w for w in mail['body'].split() if '/confirm/' in w)
    return link.split('localhost:5000')[-1]


@pytest.fixture
def applicant(client, db_session, parks, mail_out):
    """Іван: confirmed, asks for CT verification in both parks."""
    from app.models import User

    skole, uzh = parks
    _register(client, institution_ids=[skole.id, uzh.id])
    client.get(_confirm_link_path(mail_out[0]))
    db_session.expire_all()
    mail_out.clear()
    return db_session.query(User).filter_by(username='ivan').one()


def _ct_request(db_session, user):
    from app.models import VerificationRequest
    return db_session.query(VerificationRequest).filter_by(
        user_id=user.id, module='ct').one()


# ── the form ───────────────────────────────────────────────────────────────

def test_registration_page_lists_institutions(client, parks):
    body = client.get('/uk/register').get_data(as_text=True)
    assert 'НПП Ужанський' in body
    assert 'Карпати' in body, 'grouped by ecoregion'


def test_english_page_shows_english_names(client, parks):
    body = client.get('/en/register').get_data(as_text=True)
    assert 'Uzhanskyi NNP' in body


def test_picked_institutions_are_requests_not_grants(client, db_session, parks, mail_out):
    from app.models import User

    skole, uzh = parks
    _register(client, institution_ids=[skole.id, uzh.id])

    user = db_session.query(User).filter_by(username='ivan').one()
    assert user.institutions == [], 'registration never grants territory access'

    req = _ct_request(db_session, user)
    assert {r.institution.code: r.status for r in req.institution_rows()} == {
        'SKB': 'pending', 'UZH': 'pending'}


def test_both_modules_carry_the_same_institutions(client, db_session, parks, mail_out):
    from app.models import User

    skole, uzh = parks
    _register(client, wants_pam='y', institution_ids=[skole.id, uzh.id])

    user = db_session.query(User).filter_by(username='ivan').one()
    assert len(user.verification_requests) == 2
    for req in user.verification_requests:
        assert len(req.institution_rows()) == 2


def test_no_institution_picked_is_still_a_valid_signup(client, db_session, parks, mail_out):
    from app.models import User

    _register(client)
    user = db_session.query(User).filter_by(username='ivan').one()
    assert _ct_request(db_session, user).institution_rows() == []


def test_an_unknown_institution_id_is_refused(client, db_session, parks, mail_out):
    from app.models import User

    _register(client, institution_ids=[999999])
    assert db_session.query(User).filter_by(username='ivan').count() == 0, \
        'a hand-crafted POST must not slip through as a silent no-op'


# ── manager scope ──────────────────────────────────────────────────────────

def test_manager_sees_only_their_institution(app, db_session, applicant, parks,
                                             manager_of):
    skole, uzh = parks
    manager = manager_of('uzh_manager', [uzh])

    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess['_user_id'] = str(manager.id)
        sess['_fresh'] = True
    forget_cached_user()

    body = cl.get('/uk/admin/verification-requests').get_data(as_text=True)
    assert 'ivan' in body, 'the applicant named their park'
    assert 'НПП Ужанський' in body
    # The other park is shown for context but not as an actionable checkbox.
    assert 'не ваша установа' in body


def test_manager_approval_grants_only_their_park_and_keeps_the_request(
        app, db_session, applicant, parks, manager_of, mail_out):
    """The scenario from the brief: the Uzhanskyi manager approves; Іван gets
    Uzhanskyi only, and the request keeps waiting for Skole Beskids."""
    from app.models import User, VerificationRequest

    skole, uzh = parks
    manager = manager_of('uzh_manager', [uzh])
    req_id = _ct_request(db_session, applicant).id

    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess['_user_id'] = str(manager.id)
        sess['_fresh'] = True
    forget_cached_user()

    resp = cl.post(f'/uk/admin/verification-requests/{req_id}/decide',
                   data={'action': 'approve', 'institutions': [str(uzh.id)]})
    assert resp.status_code == 302

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='ivan').one()
    assert user.has_role('ct_verifier') is True, 'can verify now'
    assert [i.code for i in user.institutions] == ['UZH'], 'only the approved park'

    req = db_session.query(VerificationRequest).get(req_id)
    assert req.status == 'pending', 'Skole Beskids has not answered yet'
    assert {r.institution.code: r.status for r in req.institution_rows()} == {
        'UZH': 'approved', 'SKB': 'pending'}

    assert len(mail_out) == 1
    assert 'НПП Ужанський' in mail_out[0]['body']
    assert 'Сколівські' not in mail_out[0]['body'], \
        'the letter must not speak for a park that has not decided'


def test_the_answered_request_leaves_that_managers_queue(
        app, db_session, applicant, parks, manager_of, mail_out):
    from app.models import VerificationRequest

    skole, uzh = parks
    uzh_manager = manager_of('uzh_manager', [uzh])
    skole_manager = manager_of('skb_manager', [skole])
    req_id = _ct_request(db_session, applicant).id

    def client_for(user):
        cl = app.test_client()
        with cl.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
        return cl

    forget_cached_user()
    client_for(uzh_manager).post(
        f'/uk/admin/verification-requests/{req_id}/decide',
        data={'action': 'approve', 'institutions': [str(uzh.id)]})

    forget_cached_user()
    body = client_for(uzh_manager).get(
        '/uk/admin/verification-requests?status=pending').get_data(as_text=True)
    assert 'ivan' not in body, 'nothing left for this manager to do'

    forget_cached_user()
    body = client_for(skole_manager).get(
        '/uk/admin/verification-requests?status=pending').get_data(as_text=True)
    assert 'ivan' in body, 'still waiting for Skole Beskids'
    assert 'НПП Сколівські Бескиди' in body

    # And the second manager closes it.
    forget_cached_user()
    client_for(skole_manager).post(
        f'/uk/admin/verification-requests/{req_id}/decide',
        data={'action': 'approve', 'institutions': [str(skole.id)]})

    db_session.expire_all()
    req = db_session.query(VerificationRequest).get(req_id)
    assert req.status == 'approved'
    assert sorted(i.code for i in req.user.institutions) == ['SKB', 'UZH']


def test_manager_cannot_grant_a_park_that_is_not_theirs(
        app, db_session, applicant, parks, manager_of):
    """Posting another park's id must not grant it."""
    from app.models import User, VerificationRequest

    skole, uzh = parks
    manager = manager_of('uzh_manager', [uzh])
    req_id = _ct_request(db_session, applicant).id

    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess['_user_id'] = str(manager.id)
        sess['_fresh'] = True
    forget_cached_user()

    cl.post(f'/uk/admin/verification-requests/{req_id}/decide',
            data={'action': 'approve',
                  'institutions': [str(uzh.id), str(skole.id)]})

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='ivan').one()
    assert [i.code for i in user.institutions] == ['UZH']
    req = db_session.query(VerificationRequest).get(req_id)
    assert {r.institution.code: r.status for r in req.institution_rows()}['SKB'] == 'pending'


def test_manager_rejection_closes_only_their_park(
        app, db_session, applicant, parks, manager_of, mail_out):
    from app.models import User, VerificationRequest

    skole, uzh = parks
    manager = manager_of('uzh_manager', [uzh])
    req_id = _ct_request(db_session, applicant).id

    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess['_user_id'] = str(manager.id)
        sess['_fresh'] = True
    forget_cached_user()

    cl.post(f'/uk/admin/verification-requests/{req_id}/decide',
            data={'action': 'reject', 'institutions': [str(uzh.id)]})

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='ivan').one()
    assert user.institutions == []
    assert user.has_role('ct_verifier') is False, 'nothing approved, no role'

    req = db_session.query(VerificationRequest).get(req_id)
    assert req.status == 'pending'
    assert {r.institution.code: r.status for r in req.institution_rows()} == {
        'UZH': 'rejected', 'SKB': 'pending'}


# ── admin decisions ────────────────────────────────────────────────────────

def test_admin_approves_every_institution_at_once(auth_client, db_session,
                                                  applicant, parks, mail_out):
    from app.models import User, VerificationRequest

    skole, uzh = parks
    req_id = _ct_request(db_session, applicant).id

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req_id}/decide',
            data={'action': 'approve',
                  'institutions': [str(skole.id), str(uzh.id)]})

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='ivan').one()
    assert sorted(i.code for i in user.institutions) == ['SKB', 'UZH']
    assert db_session.query(VerificationRequest).get(req_id).status == 'approved'


def test_unticking_an_institution_removes_it_from_the_request(
        auth_client, db_session, applicant, parks, mail_out):
    from app.models import User, VerificationRequest

    skole, uzh = parks
    req_id = _ct_request(db_session, applicant).id

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req_id}/decide',
            data={'action': 'approve', 'institutions': [str(uzh.id)]})

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='ivan').one()
    assert [i.code for i in user.institutions] == ['UZH']

    req = db_session.query(VerificationRequest).get(req_id)
    assert req.status == 'approved', 'nothing is left undecided'
    assert {r.institution.code: r.status for r in req.institution_rows()} == {
        'UZH': 'approved', 'SKB': 'rejected'}


def test_admin_rejection_answers_the_whole_request(auth_client, db_session,
                                                   applicant, parks, mail_out):
    from app.models import User, VerificationRequest

    req_id = _ct_request(db_session, applicant).id

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req_id}/decide',
            data={'action': 'reject'})

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='ivan').one()
    assert user.institutions == []
    assert user.has_role('ct_verifier') is False

    req = db_session.query(VerificationRequest).get(req_id)
    assert req.status == 'rejected'
    assert {r.status for r in req.institution_rows()} == {'rejected'}


def test_granting_the_institution_by_hand_also_answers_the_row(
        auth_client, db_session, applicant, parks, mail_out):
    """An admin who grants the role and the institution in the user form must
    not leave the queue asking for access the person already has."""
    from app.models import Role, VerificationRequest

    skole, uzh = parks
    req_id = _ct_request(db_session, applicant).id
    ct_role = db_session.query(Role).filter_by(name='ct_verifier').first()
    if ct_role is None:
        ct_role = Role(name='ct_verifier')
        db_session.add(ct_role)
        db_session.commit()
    viewer_role = db_session.query(Role).filter_by(name='viewer').one()

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/users/edit/{applicant.id}', data={
        'username': 'ivan',
        'email': 'ivan@example.com',
        'first_name': 'Іван',
        'last_name': 'Франко',
        'institutions': [str(uzh.id)],
        'roles': [str(viewer_role.id), str(ct_role.id)],
    })

    db_session.expire_all()
    req = db_session.query(VerificationRequest).get(req_id)
    rows = {r.institution.code: r.status for r in req.institution_rows()}
    assert rows['UZH'] == 'approved', 'granted in the same save'
    assert rows['SKB'] == 'pending', 'never asked for, never answered'


# ── notifications ──────────────────────────────────────────────────────────

def test_managers_of_the_named_parks_are_notified(client, db_session, parks,
                                                  manager_of, mail_out):
    skole, uzh = parks
    manager_of('uzh_manager', [uzh], email='uzh@example.com')
    manager_of('other_manager', [], email='other@example.com')

    _register(client, institution_ids=[uzh.id])
    client.get(_confirm_link_path(mail_out[0]))

    recipients = {addr for m in mail_out for addr in m['to']}
    assert 'uzh@example.com' in recipients
    assert 'other@example.com' not in recipients, 'no institution in common'


def test_admins_are_notified_without_admin_email_configured(
        client, db_session, parks, make_user, mail_out, no_admin_email):
    """ADMIN_EMAIL holds at most one address and is unset on some deployments;
    the letter must reach the admin accounts themselves."""
    root = make_user(username='root_with_mail', roles=('admin',))
    root.email = 'root@example.com'
    db_session.commit()

    _register(client, institution_ids=[parks[1].id])
    client.get(_confirm_link_path(mail_out[0]))

    recipients = {addr for m in mail_out for addr in m['to']}
    assert 'root@example.com' in recipients


def test_a_decider_without_an_email_is_skipped(client, db_session, parks,
                                               manager_of, make_user, mail_out,
                                               no_admin_email):
    """Nothing to send to, and they see the request in the queue anyway."""
    from app.utils.emails import new_request_recipients
    from app.models import User

    skole, uzh = parks
    manager_of('silent_manager', [uzh], email=None)
    manager_of('loud_manager', [uzh], email='loud@example.com')

    _register(client, institution_ids=[uzh.id])
    applicant = db_session.query(User).filter_by(username='ivan').one()
    assert new_request_recipients(applicant) == ['loud@example.com']


def test_a_deactivated_manager_is_not_notified(client, db_session, parks,
                                               manager_of, mail_out,
                                               no_admin_email):
    from app.utils.emails import new_request_recipients
    from app.models import User

    skole, uzh = parks
    gone = manager_of('gone_manager', [uzh], email='gone@example.com')
    gone.is_active = False
    db_session.commit()

    _register(client, institution_ids=[uzh.id])
    applicant = db_session.query(User).filter_by(username='ivan').one()
    assert new_request_recipients(applicant) == []


# ── the applicant's own note (motivation and experience) ───────────────────

MOTIVATION = ('Працюю в парку 6 років, визначаю птахів за голосом, '
              'веду облік фотопастками з 2019.')


def test_motivation_is_stored_on_every_module_request(client, db_session, parks,
                                                      mail_out):
    from app.models import User

    _register(client, wants_pam='y', motivation=MOTIVATION)
    user = db_session.query(User).filter_by(username='ivan').one()
    assert len(user.verification_requests) == 2
    for req in user.verification_requests:
        assert req.applicant_note == MOTIVATION


def test_motivation_is_optional_and_blank_is_stored_as_none(client, db_session,
                                                            parks, mail_out):
    from app.models import User

    _register(client, motivation='   ')
    user = db_session.query(User).filter_by(username='ivan').one()
    assert _ct_request(db_session, user).applicant_note is None


def test_overlong_motivation_is_refused(client, db_session, parks, mail_out):
    from app.models import User

    resp = _register(client, motivation='я' * 2001)
    assert resp.status_code == 200, 'form is re-rendered, not accepted'
    assert db_session.query(User).count() == 0


def test_the_queue_shows_the_applicants_note(app, db_session, parks, manager_of,
                                             client, mail_out):
    from app.models import User

    skole, uzh = parks
    manager = manager_of('uzh_manager', [uzh])
    _register(client, institution_ids=[uzh.id], motivation=MOTIVATION)
    client.get(_confirm_link_path(mail_out[0]))

    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess['_user_id'] = str(manager.id)
        sess['_fresh'] = True
    forget_cached_user()

    body = cl.get('/uk/admin/verification-requests').get_data(as_text=True)
    assert 'визначаю птахів за голосом' in body


def test_the_note_reaches_the_admin_notification(client, db_session, parks,
                                                 mail_out, app):
    app.config['ADMIN_EMAIL'] = 'root@example.com'
    try:
        _register(client, motivation=MOTIVATION)
        client.get(_confirm_link_path(mail_out[0]))
    finally:
        app.config.pop('ADMIN_EMAIL', None)

    admin_mail = next(m for m in mail_out if m['to'] == ['root@example.com'])
    assert MOTIVATION in admin_mail['body']
