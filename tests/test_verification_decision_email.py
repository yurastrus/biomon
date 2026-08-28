# -*- coding: utf-8 -*-
"""A user must learn, by email, when their verification rights are granted.

There are two ways rights reach a person and both have to tell them:

  * the queue — an admin approves (or rejects) the request they filed at
    registration, in /admin/verification-requests;
  * the user form — an admin ticks ct_verifier/pam_verifier in
    /admin/users/edit/<id>, which is also how a queue applicant often gets
    approved in practice.

Neither path had any test, so a silent regression in either was invisible.
Emails are asserted at the app.utils.emails.send_email boundary — SMTP is never
touched, same as tests/test_registration.py.
"""
from datetime import datetime
from unittest.mock import patch

import pytest


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


@pytest.fixture
def applicant(db_session, make_user):
    """A confirmed self-registered user with a pending ct request."""
    from app.extensions import db
    from app.models import VerificationRequest

    user = make_user(username='applicant', roles=[])
    user.email = 'applicant@example.com'
    user.first_name, user.last_name = 'Тарас', 'Шевченко'
    user.email_confirmed_at = datetime.utcnow()
    db.session.add(VerificationRequest(user_id=user.id, module='ct',
                                       status=VerificationRequest.STATUS_PENDING,
                                       requested_at=datetime.utcnow()))
    db.session.commit()
    return user


def _decide(client, request_id, action, note=None):
    data = {'action': action}
    if note is not None:
        data['note'] = note
    return client.post(f'/uk/admin/verification-requests/{request_id}/decide',
                       data=data, follow_redirects=True)


def _pending_id(user):
    from app.models import VerificationRequest
    return VerificationRequest.query.filter_by(user_id=user.id).one().id


# ── the queue ───────────────────────────────────────────────────────────────

def test_approval_emails_the_applicant(auth_client, applicant, mail_out):
    from app.models import VerificationRequest

    req_id = _pending_id(applicant)
    resp = _decide(auth_client(role='admin'), req_id, 'approve')
    assert resp.status_code == 200

    assert len(mail_out) == 1
    mail = mail_out[0]
    assert mail['to'] == ['applicant@example.com']
    assert 'Права верифікації надано' in mail['subject']
    assert 'фотопаст' in mail['body']

    req = VerificationRequest.query.get(req_id)
    assert req.status == VerificationRequest.STATUS_APPROVED
    assert 'ct_verifier' in {r.name for r in req.user.roles}


def test_rejection_emails_the_applicant_with_the_note(auth_client, applicant, mail_out):
    req_id = _pending_id(applicant)
    _decide(auth_client(role='admin'), req_id, 'reject', note='бракує досвіду')

    assert len(mail_out) == 1
    assert 'відхилено' in mail_out[0]['subject']
    assert 'бракує досвіду' in mail_out[0]['body']


def test_english_applicant_gets_an_english_letter(auth_client, applicant, mail_out):
    from app.extensions import db

    applicant.locale = 'en'
    db.session.commit()
    _decide(auth_client(role='admin'), _pending_id(applicant), 'approve')

    assert mail_out[0]['subject'] == 'Verification rights granted — biomon'


def test_deciding_twice_does_not_send_a_second_letter(auth_client, applicant, mail_out):
    req_id = _pending_id(applicant)
    client = auth_client(role='admin')
    _decide(client, req_id, 'approve')
    _decide(client, req_id, 'reject')

    assert len(mail_out) == 1


def test_applicant_without_an_address_does_not_break_the_decision(
        auth_client, applicant, mail_out):
    from app.extensions import db
    from app.models import VerificationRequest

    applicant.email = None
    db.session.commit()

    req_id = _pending_id(applicant)
    resp = _decide(auth_client(role='admin'), req_id, 'approve')

    assert resp.status_code == 200
    assert mail_out == []
    assert VerificationRequest.query.get(req_id).status == \
        VerificationRequest.STATUS_APPROVED


# ── the user form ───────────────────────────────────────────────────────────

def _edit_user(client, user, role_ids):
    """POST the user form with the given roles, leaving the rest untouched."""
    return client.post(
        f'/uk/admin/users/edit/{user.id}',
        data={
            'username': user.username,
            'email': user.email or '',
            'phone': '',
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'password': '',
            'roles': [str(r) for r in role_ids],
        },
        follow_redirects=True)


def test_granting_the_role_by_hand_emails_the_user(
        auth_client, applicant, make_role, mail_out):
    role = make_role('ct_verifier')
    resp = _edit_user(auth_client(role='admin'), applicant, [role.id])
    assert resp.status_code == 200

    assert len(mail_out) == 1
    assert mail_out[0]['to'] == ['applicant@example.com']
    assert 'Права верифікації надано' in mail_out[0]['subject']
    assert 'фотопаст' in mail_out[0]['body']


def test_granting_by_hand_closes_the_pending_request(
        auth_client, applicant, make_role, mail_out):
    """Otherwise the queue keeps showing work that is already done — and
    approving it there later would send the applicant a second letter."""
    from app.models import VerificationRequest

    role = make_role('ct_verifier')
    _edit_user(auth_client(role='admin'), applicant, [role.id])

    req = VerificationRequest.query.filter_by(user_id=applicant.id).one()
    assert req.status == VerificationRequest.STATUS_APPROVED
    assert req.decided_at is not None
    assert req.decided_by_id is not None


def test_saving_the_form_again_does_not_re_email(
        auth_client, applicant, make_role, mail_out):
    role = make_role('ct_verifier')
    client = auth_client(role='admin')
    _edit_user(client, applicant, [role.id])
    _edit_user(client, applicant, [role.id])

    assert len(mail_out) == 1


def test_granting_an_unrelated_role_sends_nothing(
        auth_client, applicant, make_role, mail_out):
    role = make_role('analyst')
    _edit_user(auth_client(role='admin'), applicant, [role.id])

    assert mail_out == []


def test_both_modules_granted_at_once_produce_one_letter(
        auth_client, applicant, make_role, mail_out):
    ct = make_role('ct_verifier')
    pam = make_role('pam_verifier')
    _edit_user(auth_client(role='admin'), applicant, [ct.id, pam.id])

    assert len(mail_out) == 1
    body = mail_out[0]['body']
    assert 'фотопаст' in body and 'звукозап' in body


def test_grant_without_an_address_does_not_break_the_save(
        auth_client, applicant, make_role, db_session, mail_out):
    from app.extensions import db

    applicant.email = None
    db.session.commit()

    role = make_role('ct_verifier')
    resp = _edit_user(auth_client(role='admin'), applicant, [role.id])

    assert resp.status_code == 200
    assert mail_out == []
    assert 'ct_verifier' in {r.name for r in applicant.roles}
