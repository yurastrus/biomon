"""Per-user email notification opt-outs.

Covers the three things the feature promises:
  • the checkbox defaults to ON and round-trips through /uk/profile
  • only the account owner and an admin may flip it (a manager may not)
  • the weekly camera-trap reminder skips opted-out users and tells the
    subscribed ones how to switch it off
"""
from unittest.mock import patch

import pytest

from app.models import User
from app.utils import notification_prefs as prefs


CT_STATS = {'series': 0, 'identifications': 0, 'species_count': 0, 'top_species': []}
PAM_STATS = {'verifications': 0, 'positive': 0, 'positive_rate': 0.0,
             'species_count': 0, 'top_species': []}


@pytest.fixture
def stats_patched():
    with patch('app.camera_traps.utils.get_user_ct_stats', return_value=CT_STATS), \
         patch('app.pam.utils.get_user_pam_stats', return_value=PAM_STATS):
        yield


# ── model / registry ──────────────────────────────────────────────────────────

def test_new_user_is_subscribed_by_default(make_user):
    """An opt-OUT must never start switched off, or nobody would ever be mailed."""
    u = make_user(username='freshuser', roles=('viewer',))
    assert u.notify_ct_pending is True
    assert prefs.is_enabled(u, 'ct_pending') is True


def test_registry_columns_exist_on_user(make_user):
    """Every registered preference must map to a real column."""
    u = make_user(username='regcheck', roles=('viewer',))
    for pref in prefs.NOTIFICATION_PREFS:
        assert hasattr(u, pref.column), f'User has no column {pref.column}'


def test_is_enabled_unknown_key_defaults_to_true(make_user):
    u = make_user(username='unknownkey', roles=('viewer',))
    assert prefs.is_enabled(u, 'no_such_notification') is True


# ── profile page: the owner edits their own ───────────────────────────────────

def test_profile_shows_the_checkbox_checked(auth_client, db_session, stats_patched):
    cl = auth_client(role='viewer', username='boxuser')
    body = cl.get('/uk/profile').data.decode('utf-8')
    assert 'notify_ct_pending' in body
    assert 'checked' in body


def test_owner_can_switch_it_off_and_back_on(auth_client, db_session, stats_patched):
    cl = auth_client(role='viewer', username='optout')

    # Unchecked checkboxes send nothing at all — absence is the "off" signal.
    resp = cl.post('/uk/profile', data={
        'submit_notifications': 'Зберегти налаштування сповіщень',
    })
    assert resp.status_code in (302, 303)
    assert User.query.filter_by(username='optout').first().notify_ct_pending is False

    resp = cl.post('/uk/profile', data={
        'notify_ct_pending': 'y',
        'submit_notifications': 'Зберегти налаштування сповіщень',
    })
    assert resp.status_code in (302, 303)
    assert User.query.filter_by(username='optout').first().notify_ct_pending is True


def test_other_profile_forms_do_not_touch_the_preference(auth_client, db_session,
                                                         stats_patched):
    """A username change posts no notify_* fields; that must not unsubscribe."""
    cl = auth_client(role='viewer', username='renameme')
    cl.post('/uk/profile', data={
        'new_username': 'renamed',
        'submit_username': 'Змінити логін',
    })
    assert User.query.filter_by(username='renamed').first().notify_ct_pending is True


# ── admin form: admin may edit, manager may not ───────────────────────────────

def _admin_post(cl, user, **extra):
    """POST the admin user form with the fields it always submits."""
    data = {
        'username': user.username,
        'email': user.email or '',
        'phone': '',
        'first_name': '',
        'last_name': '',
        'password': '',
    }
    data.update(extra)
    return cl.post(f'/uk/admin/users/edit/{user.id}', data=data,
                   follow_redirects=False)


def test_admin_sees_and_can_clear_the_checkbox(auth_client, db_session, make_user):
    target = make_user(username='mailed', roles=('ct_verifier',))
    cl = auth_client(role='admin', username='the_admin')

    body = cl.get(f'/uk/admin/users/edit/{target.id}').data.decode('utf-8')
    assert 'notify_ct_pending' in body

    _admin_post(cl, target)  # checkbox omitted → off
    assert User.query.get(target.id).notify_ct_pending is False


def test_manager_neither_sees_nor_can_clear_it(auth_client, db_session, make_user):
    """The section is hidden from managers, so their POST must be a no-op here —
    otherwise the missing checkbox would silently unsubscribe the user."""
    target = make_user(username='managed', roles=('viewer',))
    cl = auth_client(role='manager', username='the_manager')

    body = cl.get(f'/uk/admin/users/edit/{target.id}').data.decode('utf-8')
    assert 'notify_ct_pending' not in body

    # Even a hand-crafted POST that omits the field leaves the preference alone.
    _admin_post(cl, target)
    assert User.query.get(target.id).notify_ct_pending is True


# ── the sender honours the flag ───────────────────────────────────────────────

def test_reminder_skips_opted_out_user(app, db_session, make_user):
    from app.camera_traps.notifications import send_identification_reminders

    u = make_user(username='quiet', roles=('ct_verifier',))
    u.email = 'quiet@example.org'
    u.notify_ct_pending = False
    db_session.commit()

    with app.app_context(), \
         patch('app.camera_traps.notifications._count_pending_for_user',
               return_value=999) as counter, \
         patch('app.camera_traps.notifications._send_reminder_email') as sender:
        sent, _ = send_identification_reminders()

    assert sent == 0
    assert not sender.called
    # Dropped before counting — an unsubscribe holds however big the backlog is.
    assert not counter.called


def test_reminder_still_reaches_subscribed_user(app, db_session, make_user):
    from app.camera_traps.notifications import send_identification_reminders

    u = make_user(username='loud', roles=('ct_verifier',))
    u.email = 'loud@example.org'
    db_session.commit()

    with app.app_context(), \
         patch('app.camera_traps.notifications._count_pending_for_user',
               return_value=42), \
         patch('app.camera_traps.notifications._send_reminder_email') as sender:
        sent, _ = send_identification_reminders()

    assert sent == 1
    assert sender.call_args.args[0].username == 'loud'


def test_reminder_body_explains_how_to_unsubscribe(app, db_session, make_user):
    """The letter is the only place the opt-out is discoverable — it must name
    the profile page, the section and the button."""
    from app.camera_traps.notifications import _send_reminder_email

    u = make_user(username='reader', roles=('ct_verifier',))
    u.email = 'reader@example.org'
    db_session.commit()

    with app.app_context(), patch('app.camera_traps.notifications.mail') as mail:
        _send_reminder_email(u, 15)

    body = mail.send.call_args.args[0].body
    assert '/uk/profile' in body
    assert 'Сповіщення' in body
    assert 'галочку' in body
