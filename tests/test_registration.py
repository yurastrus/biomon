"""
Self-service registration: the public flow and both gates around it.

What is pinned here:
  * registration creates an INACTIVE, self-registered account with the `viewer`
    role, NO institutions, and one pending VerificationRequest per chosen module;
  * login is refused until the emailed address is confirmed;
  * the confirmation link is signed, expiring, single-purpose and idempotent;
  * the admin is notified only AFTER confirmation (bots never reach the inbox);
  * approval grants exactly one role and still no institutions — which is what
    limits a fresh verifier to public locations;
  * bot defences (honeypot, duplicate username/email) and the enumeration-safe
    resend endpoint.

Emails are asserted at the app.utils.emails.send_email boundary — SMTP is never
touched. Flask-Login caches the user on ``g``, which outlives a request inside a
test's app context, so tests that switch identity call ``forget_cached_user()``.

Run:
    venv/Scripts/python -m pytest tests/test_registration.py -v
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from flask import g


REG_DATA = {
    'first_name': 'Тарас',
    'last_name': 'Шевченко',
    'email': 'taras@example.com',
    'username': 'taras',
    'password': 'parol12345',
    'confirm_password': 'parol12345',
    'wants_ct': 'y',
    'consent': 'y',
}


def forget_cached_user():
    """Drop Flask-Login's per-app-context user cache (see module docstring)."""
    g.pop('_login_user', None)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Registration/resend are rate-limited per IP; the in-memory counters are
    shared by the session-scoped app, so reset them between tests (same approach
    as tests/test_login_hardening.py)."""
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
    """Capture outgoing emails instead of queuing SMTP deliveries."""
    sent = []

    def _fake(subject, recipients, body):
        sent.append({'subject': subject, 'to': list(recipients), 'body': body})
        return True

    with patch('app.utils.emails.send_email', side_effect=_fake), \
         patch('app.utils.notifications.send_notification'):
        yield sent


def _register(client, **overrides):
    data = dict(REG_DATA)
    data.update(overrides)
    return client.post('/uk/register', data=data)


def _confirm_link_path(mail):
    """Extract the /uk/confirm/<token> path from a confirmation email body."""
    link = next(w for w in mail['body'].split() if '/confirm/' in w)
    return link.split('localhost:5000')[-1]


# ── page availability ───────────────────────────────────────────────────────

@pytest.mark.parametrize('path', ['/uk/register', '/en/register',
                                  '/uk/resend-confirmation'])
def test_public_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_login_page_links_to_registration(client):
    body = client.get('/uk/login').get_data(as_text=True)
    assert '/uk/register' in body
    assert '/uk/resend-confirmation' in body


# ── happy path ─────────────────────────────────────────────────────────────

def test_registration_creates_inactive_account_with_pending_request(client, db_session, mail_out):
    from app.models import User, VerificationRequest

    resp = _register(client, wants_pam='y')
    assert resp.status_code == 302
    assert '/uk/login' in resp.headers['Location']

    user = db_session.query(User).filter_by(username='taras').one()
    assert user.is_active is False, 'a fresh signup must not be able to log in'
    assert user.is_email_confirmed is False
    assert user.self_registered is True
    assert user.locale == 'uk'
    assert [r.name for r in user.roles] == ['viewer']
    assert user.institutions == [], 'no institutions = public locations only'

    assert sorted((r.module, r.status) for r in user.verification_requests) == [
        ('ct', 'pending'), ('pam', 'pending')]

    assert len(mail_out) == 1
    assert mail_out[0]['to'] == ['taras@example.com']
    assert '/uk/confirm/' in mail_out[0]['body']


def test_registration_lowercases_email_and_keeps_names(client, db_session, mail_out):
    from app.models import User

    _register(client, email='TaRaS@Example.COM')
    user = db_session.query(User).filter_by(username='taras').one()
    assert user.email == 'taras@example.com'
    assert user.full_name == 'Тарас Шевченко'


def test_english_registration_sends_english_email(client, db_session, mail_out):
    from app.models import User

    data = dict(REG_DATA)
    client.post('/en/register', data=data)
    user = db_session.query(User).filter_by(username='taras').one()
    assert user.locale == 'en'
    assert 'Confirm your registration' in mail_out[0]['subject']
    assert '/en/confirm/' in mail_out[0]['body']


def test_confirmation_activates_account_and_notifies_admin(client, db_session, mail_out):
    from app.models import User

    _register(client)
    with patch('app.routes.main.notify_admin_new_requests') as notify:
        resp = client.get(_confirm_link_path(mail_out[0]))
    assert resp.status_code == 302

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='taras').one()
    assert user.is_active is True
    assert user.is_email_confirmed is True
    assert notify.call_count == 1, 'admin is notified once, only after confirmation'
    assert notify.call_args[0][1] == ['ct']


def test_admin_is_not_notified_before_confirmation(client, db_session, mail_out):
    with patch('app.routes.main.notify_admin_new_requests') as notify:
        _register(client)
    assert notify.call_count == 0


def test_confirmation_is_idempotent(client, db_session, mail_out):
    from app.models import User

    _register(client)
    path = _confirm_link_path(mail_out[0])
    client.get(path)
    db_session.expire_all()
    first = db_session.query(User).filter_by(username='taras').one().email_confirmed_at

    resp = client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    assert 'вже підтверджена' in resp.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.query(User).filter_by(username='taras').one().email_confirmed_at == first


# ── login gate ─────────────────────────────────────────────────────────────

def test_login_refused_before_confirmation(client, db_session, mail_out):
    _register(client)
    resp = client.post('/uk/login', data={'username': 'taras', 'password': 'parol12345'})
    assert resp.status_code == 302
    assert '/uk/resend-confirmation' in resp.headers['Location']


def test_login_works_after_confirmation(client, db_session, mail_out):
    _register(client)
    client.get(_confirm_link_path(mail_out[0]))
    forget_cached_user()
    resp = client.post('/uk/login', data={'username': 'taras', 'password': 'parol12345'})
    assert resp.status_code == 302
    assert '/uk/resend-confirmation' not in resp.headers['Location']


def test_deactivated_account_cannot_log_in(client, db_session, make_user):
    """An admin-disabled account is refused with its own message (not the
    "confirm your email" one), because it was never self-registered."""
    user = make_user(username='disabled', password='parol12345')
    user.is_active = False
    db_session.commit()

    resp = client.post('/uk/login', data={'username': 'disabled',
                                          'password': 'parol12345'})
    assert resp.status_code == 200
    assert 'деактивовано' in resp.get_data(as_text=True)


def test_deactivating_a_user_invalidates_their_session(auth_client, db_session):
    """load_user drops the session of a disabled account mid-flight — Flask-Login
    itself only checks is_active at login time."""
    from app.models import User

    cl = auth_client(role='viewer', username='tobedisabled')
    assert cl.get('/uk/profile').status_code == 200

    db_session.query(User).filter_by(username='tobedisabled').one().is_active = False
    db_session.commit()
    forget_cached_user()

    resp = cl.get('/uk/profile')
    assert resp.status_code == 302, 'disabled account must be logged out'


# ── token handling ─────────────────────────────────────────────────────────

def test_tampered_token_is_rejected(client, db_session, mail_out):
    from app.models import User

    _register(client)
    path = _confirm_link_path(mail_out[0])
    resp = client.get(path[:-3] + 'xyz')
    assert '/uk/resend-confirmation' in resp.headers['Location']
    db_session.expire_all()
    assert db_session.query(User).filter_by(username='taras').one().is_active is False


def test_expired_token_is_rejected(app, client, db_session, mail_out):
    from app.utils.tokens import generate_email_token, verify_email_token

    with app.test_request_context('/'):
        token = generate_email_token('taras@example.com')
        assert verify_email_token(token, max_age=3600) == 'taras@example.com'
        assert verify_email_token(token, max_age=-1) is None


def test_token_from_another_salt_is_rejected(app):
    """A token signed for a different purpose must not confirm an address."""
    from itsdangerous import URLSafeTimedSerializer
    from app.utils.tokens import verify_email_token

    with app.test_request_context('/'):
        other = URLSafeTimedSerializer(app.config['SECRET_KEY']).dumps(
            'taras@example.com', salt='some-other-purpose')
        assert verify_email_token(other) is None


def test_confirmation_for_deleted_account_does_not_crash(app, client, db_session):
    from app.utils.tokens import generate_email_token

    with app.test_request_context('/'):
        token = generate_email_token('ghost@example.com')
    resp = client.get(f'/uk/confirm/{token}')
    assert resp.status_code == 302
    assert '/uk/resend-confirmation' in resp.headers['Location']


# ── validation and bot defences ────────────────────────────────────────────

def test_honeypot_field_blocks_the_signup(client, db_session, mail_out):
    from app.models import User

    resp = _register(client, website='http://spam.example')
    assert resp.status_code == 200, 'form is re-rendered, not accepted'
    assert db_session.query(User).count() == 0
    assert mail_out == []


def test_registration_is_rate_limited_per_ip(client, db_session, mail_out):
    """Sixth signup attempt from one IP within the hour is refused (5/hour)."""
    from app.models import User

    for i in range(5):
        _register(client, username=f'user{i}', email=f'user{i}@example.com')
    resp = _register(client, username='user6', email='user6@example.com')
    assert resp.status_code == 429
    assert db_session.query(User).count() == 5


def test_at_least_one_module_is_required(client, db_session):
    from app.models import User

    data = dict(REG_DATA)
    data.pop('wants_ct')
    resp = client.post('/uk/register', data=data)
    assert resp.status_code == 200
    assert 'хоча б один' in resp.get_data(as_text=True)
    assert db_session.query(User).count() == 0


def test_consent_is_required(client, db_session):
    from app.models import User

    data = dict(REG_DATA)
    data.pop('consent')
    client.post('/uk/register', data=data)
    assert db_session.query(User).count() == 0


@pytest.mark.parametrize('field,value', [
    ('password', 'short1'),          # below PASSWORD_MIN_LENGTH
    ('password', 'onlyletters'),     # no digits
    ('username', 'ab'),              # too short
    ('username', 'кирилиця'),        # not in the allowed charset
    ('email', 'not-an-email'),
])
def test_invalid_input_is_rejected(client, db_session, field, value):
    from app.models import User

    overrides = {field: value}
    if field == 'password':
        overrides['confirm_password'] = value
    _register(client, **overrides)
    assert db_session.query(User).count() == 0


def test_duplicate_username_is_rejected(client, db_session, make_user, mail_out):
    from app.models import User

    make_user(username='taras')
    resp = _register(client)
    assert resp.status_code == 200
    assert 'вже зайняте' in resp.get_data(as_text=True)
    assert db_session.query(User).filter_by(email='taras@example.com').count() == 0


def test_duplicate_email_is_rejected_case_insensitively(client, db_session, mail_out):
    from app.models import User

    _register(client)
    resp = _register(client, username='taras2', email='TARAS@example.com')
    assert resp.status_code == 200
    assert 'вже зареєстрована' in resp.get_data(as_text=True)
    assert db_session.query(User).count() == 1


def test_registering_while_logged_in_redirects_to_profile(auth_client):
    cl = auth_client(role='viewer', username='already_in')
    # Anonymous requests earlier in the run can leave an AnonymousUser cached on
    # `g` (see the module docstring); without dropping it this assertion is
    # order-dependent and fails intermittently in a full-suite run.
    forget_cached_user()
    resp = cl.get('/uk/register')
    assert resp.status_code == 302
    assert '/profile' in resp.headers['Location']


# ── resend confirmation ────────────────────────────────────────────────────

def test_resend_sends_a_new_link_for_an_unconfirmed_account(client, db_session, mail_out):
    _register(client)
    mail_out.clear()
    resp = client.post('/uk/resend-confirmation', data={'email': 'taras@example.com'})
    assert resp.status_code == 302
    assert len(mail_out) == 1
    assert '/uk/confirm/' in mail_out[0]['body']


@pytest.mark.parametrize('email', ['nobody@example.com', 'taras@example.com'])
def test_resend_never_reveals_whether_an_address_exists(client, db_session, mail_out, email):
    """Same response for unknown and already-confirmed addresses — no oracle."""
    _register(client)
    client.get(_confirm_link_path(mail_out[0]))   # taras is now confirmed
    mail_out.clear()

    resp = client.post('/uk/resend-confirmation', data={'email': email},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert 'ми надіслали лист ще раз' in resp.get_data(as_text=True)
    assert mail_out == [], 'no mail for confirmed or unknown addresses'


# ── admin decisions ────────────────────────────────────────────────────────

@pytest.fixture
def confirmed_applicant(client, db_session, mail_out):
    """A confirmed self-registered user asking for both modules."""
    from app.models import User

    _register(client, wants_pam='y')
    client.get(_confirm_link_path(mail_out[0]))
    db_session.expire_all()
    mail_out.clear()
    return db_session.query(User).filter_by(username='taras').one()


def test_queue_lists_confirmed_applicants_only(auth_client, db_session, client, mail_out):
    from app.models import User

    # confirmed applicant
    _register(client)
    client.get(_confirm_link_path(mail_out[0]))
    # unconfirmed applicant
    mail_out.clear()
    _register(client, username='ivan', email='ivan@example.com')

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    body = cl.get('/uk/admin/verification-requests').get_data(as_text=True)
    assert 'taras' in body
    assert 'ivan' not in body, 'unconfirmed signups must not fill the admin queue'


def test_approval_grants_the_role_and_no_institutions(auth_client, db_session,
                                                      confirmed_applicant, mail_out):
    from app.models import User, VerificationRequest

    req = db_session.query(VerificationRequest).filter_by(
        user_id=confirmed_applicant.id, module='ct').one()

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    resp = cl.post(f'/uk/admin/verification-requests/{req.id}/decide',
                   data={'action': 'approve', 'note': 'знайомий біолог'})
    assert resp.status_code == 302

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='taras').one()
    assert user.has_role('ct_verifier') is True
    assert user.has_role('pam_verifier') is False, 'only the approved module'
    assert user.institutions == [], 'approval never grants territory access'

    req = db_session.query(VerificationRequest).get(req.id)
    assert req.status == 'approved'
    assert req.decided_by.username == 'root'
    assert req.decided_at is not None
    assert req.note == 'знайомий біолог'

    assert len(mail_out) == 1
    assert mail_out[0]['to'] == ['taras@example.com']
    assert 'надано' in mail_out[0]['subject'].lower()


def test_rejection_grants_nothing(auth_client, db_session, confirmed_applicant, mail_out):
    from app.models import User, VerificationRequest

    req = db_session.query(VerificationRequest).filter_by(
        user_id=confirmed_applicant.id, module='pam').one()

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req.id}/decide',
            data={'action': 'reject', 'note': 'поки без досвіду'})

    db_session.expire_all()
    user = db_session.query(User).filter_by(username='taras').one()
    assert user.has_role('pam_verifier') is False
    assert db_session.query(VerificationRequest).get(req.id).status == 'rejected'
    assert len(mail_out) == 1
    assert 'відхилено' in mail_out[0]['subject'].lower()


def test_a_decided_request_cannot_be_decided_again(auth_client, db_session,
                                                   confirmed_applicant, mail_out):
    from app.models import VerificationRequest

    req = db_session.query(VerificationRequest).filter_by(
        user_id=confirmed_applicant.id, module='ct').one()

    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req.id}/decide', data={'action': 'reject'})
    forget_cached_user()
    resp = cl.post(f'/uk/admin/verification-requests/{req.id}/decide',
                   data={'action': 'approve'}, follow_redirects=True)

    assert 'уже опрацьовано' in resp.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.query(VerificationRequest).get(req.id).status == 'rejected'


def test_unknown_action_changes_nothing(auth_client, db_session, confirmed_applicant):
    from app.models import VerificationRequest

    req = db_session.query(VerificationRequest).filter_by(
        user_id=confirmed_applicant.id, module='ct').one()
    cl = auth_client(role='admin', username='root')
    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req.id}/decide', data={'action': 'nonsense'})
    db_session.expire_all()
    assert db_session.query(VerificationRequest).get(req.id).status == 'pending'


@pytest.mark.parametrize('role', ['viewer', 'ct_verifier', 'analyst'])
def test_non_managers_cannot_see_or_decide_requests(auth_client, db_session,
                                                    confirmed_applicant, role):
    from app.models import VerificationRequest

    req = db_session.query(VerificationRequest).filter_by(
        user_id=confirmed_applicant.id, module='ct').one()

    cl = auth_client(role=role, username=f'someone_{role}')
    forget_cached_user()
    assert cl.get('/uk/admin/verification-requests').status_code == 403
    forget_cached_user()
    assert cl.post(f'/uk/admin/verification-requests/{req.id}/decide',
                   data={'action': 'approve'}).status_code == 403

    db_session.expire_all()
    assert db_session.query(VerificationRequest).get(req.id).status == 'pending'


def test_manager_cannot_decide_a_request_without_their_institution(
        auth_client, db_session, confirmed_applicant):
    """The queue is open to managers, but a request naming no institution of
    theirs is not theirs to answer — approving it would grant a site-wide
    verifier role they do not own."""
    from app.models import VerificationRequest

    req = db_session.query(VerificationRequest).filter_by(
        user_id=confirmed_applicant.id, module='ct').one()

    cl = auth_client(role='manager', username='lonely_manager')
    forget_cached_user()
    body = cl.get('/uk/admin/verification-requests').get_data(as_text=True)
    assert 'taras' not in body, 'not their applicant'

    forget_cached_user()
    cl.post(f'/uk/admin/verification-requests/{req.id}/decide',
            data={'action': 'approve'})
    db_session.expire_all()
    assert db_session.query(VerificationRequest).get(req.id).status == 'pending'


def test_profile_shows_request_status(client, db_session, confirmed_applicant, app):
    cl = app.test_client()
    with cl.session_transaction() as sess:
        sess['_user_id'] = str(confirmed_applicant.id)
        sess['_fresh'] = True
    forget_cached_user()

    body = cl.get('/uk/profile').get_data(as_text=True)
    assert 'Права верифікації' in body
    assert 'Очікує розгляду' in body


# ── housekeeping ───────────────────────────────────────────────────────────

def test_purge_removes_only_stale_unconfirmed_accounts(app, db_session, client, mail_out):
    from app.models import User, VerificationRequest
    from app.utils.registration import purge_unconfirmed_users

    _register(client)                                       # stale, unconfirmed
    _register(client, username='fresh', email='fresh@example.com')
    _register(client, username='done', email='done@example.com')

    stale = db_session.query(User).filter_by(username='taras').one()
    stale.created_at = datetime.utcnow() - timedelta(days=30)
    done = db_session.query(User).filter_by(username='done').one()
    done.created_at = datetime.utcnow() - timedelta(days=30)
    done.email_confirmed_at = datetime.utcnow()
    db_session.commit()

    assert purge_unconfirmed_users(max_age_days=7) == 1

    names = {u.username for u in db_session.query(User).all()}
    assert names == {'fresh', 'done'}
    assert db_session.query(VerificationRequest).filter_by(user_id=stale.id).count() == 0, \
        'requests must go with the account'


def test_get_or_create_role_is_idempotent(db_session):
    from app.utils.registration import get_or_create_role

    first = get_or_create_role('ct_verifier')
    db_session.commit()
    second = get_or_create_role('ct_verifier')
    assert first.id == second.id


def test_create_self_registered_user_requires_a_module(db_session):
    from app.utils.registration import create_self_registered_user

    with pytest.raises(ValueError):
        create_self_registered_user(
            username='x', email='x@example.com', password='parol12345',
            first_name='X', last_name='Y', modules=['nonsense'])
