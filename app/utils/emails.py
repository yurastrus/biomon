# SPDX-License-Identifier: AGPL-3.0-only
"""Transactional emails for self-service registration.

Delivery is fire-and-forget in a background thread: Flask-Mail 0.10 has no SMTP
timeout, so sending inline would let a slow mail server hold a gunicorn worker
open for as long as the OS allows. The user-visible remedy for a mail that never
arrives is the "resend confirmation" page, not a blocked request.

Bodies are plain text and are written in the recipient's language (``locale`` on
the User, captured at registration) because these are generated outside a
request — there is no ``g.lang_code`` to fall back on.
"""
from threading import Thread

from flask import current_app, url_for
from flask_mail import Message

from app.extensions import mail


def _deliver(app, msg):
    """Send one message inside an app context; never raise into the caller."""
    with app.app_context():
        try:
            mail.send(msg)
            current_app.logger.info("Email sent to %s: %s", msg.recipients, msg.subject)
        except Exception as e:
            current_app.logger.error(
                "Email delivery failed (to=%s subject=%r): %s", msg.recipients, msg.subject, e
            )


def send_email(subject, recipients, body):
    """Queue an email for delivery. No-op (logged) when mail is not configured."""
    app = current_app._get_current_object()
    if not app.config.get('MAIL_SERVER'):
        app.logger.info("Mail not configured — skipping email to %s (%r)", recipients, subject)
        return False
    msg = Message(subject=subject, recipients=list(recipients), body=body)
    Thread(target=_deliver, args=(app, msg), daemon=True).start()
    return True


def _lang(user):
    return 'en' if (user.locale or 'uk') == 'en' else 'uk'


def _site_url():
    return current_app.config.get('SITE_URL', 'http://localhost:5000').rstrip('/')


def send_confirmation_email(user, token):
    """Send the "confirm your address" link to a freshly registered user."""
    lang = _lang(user)
    link = f"{_site_url()}{url_for('main.confirm_email', lang_code=lang, token=token)}"
    hours = int(current_app.config.get('EMAIL_CONFIRM_TOKEN_MAX_AGE', 86400) / 3600)

    if lang == 'en':
        subject = "Confirm your registration — biomon"
        body = (
            f"Hello, {user.full_name}!\n\n"
            "You (or someone using your address) registered at biomon.app.\n"
            "Confirm your address to activate the account:\n\n"
            f"{link}\n\n"
            f"The link is valid for {hours} hours. If you did not register, "
            "simply ignore this message — the account stays inactive and is "
            "removed automatically.\n\n"
            "After confirming, your request for verification rights goes to an "
            "administrator. You will get another email once it is decided.\n"
        )
    else:
        subject = "Підтвердіть реєстрацію — biomon"
        body = (
            f"Вітаємо, {user.full_name}!\n\n"
            "Ви (або хтось із вашою адресою) зареєструвалися на biomon.app.\n"
            "Підтвердіть адресу, щоб активувати акаунт:\n\n"
            f"{link}\n\n"
            f"Посилання дійсне {hours} год. Якщо ви не реєструвалися — просто "
            "проігноруйте цей лист: акаунт залишиться неактивним і буде "
            "видалений автоматично.\n\n"
            "Після підтвердження ваш запит на права верифікації побачить "
            "адміністратор. Про рішення ми напишемо окремим листом.\n"
        )
    return send_email(subject, [user.email], body)


_MODULE_LABEL = {
    'ct': {'uk': 'верифікація фото з фотопасток', 'en': 'camera-trap photo verification'},
    'pam': {'uk': 'верифікація звукозаписів', 'en': 'audio recording verification'},
}


def send_decision_email(user, module, approved, note=None, institutions=None):
    """Tell the user that their verification request was approved or rejected.

    ``institutions`` names the institutions granted by this decision. A request
    can be approved one institution at a time (each by its own manager), so the
    letter says what was opened now and stays silent about what is still being
    decided — the profile page shows the full picture.
    """
    lang = _lang(user)
    label = _MODULE_LABEL[module][lang]
    inst_line = ', '.join(institutions) if institutions else None

    if lang == 'en':
        if approved:
            subject = "Verification rights granted — biomon"
            if inst_line:
                body = (
                    f"Hello, {user.full_name}!\n\n"
                    f"Your request was approved: {label}.\n\n"
                    f"You now have access to the data of: {inst_line}.\n"
                    "Any other institution you asked for is still deciding; you "
                    "will get a separate letter about each.\n\n"
                    f"Start here: {_site_url()}/en/profile\n"
                )
            else:
                body = (
                    f"Hello, {user.full_name}!\n\n"
                    f"Your request was approved: {label}.\n\n"
                    "You now have access to publicly available locations. Access to "
                    "other territories is granted separately by their institution.\n\n"
                    f"Start here: {_site_url()}/en/profile\n"
                )
        else:
            subject = "Verification request declined — biomon"
            body = (
                f"Hello, {user.full_name}!\n\n"
                f"Your request was declined: {label}.\n"
            )
    else:
        if approved:
            subject = "Права верифікації надано — biomon"
            if inst_line:
                body = (
                    f"Вітаємо, {user.full_name}!\n\n"
                    f"Ваш запит підтверджено: {label}.\n\n"
                    f"Вам відкрито доступ до даних: {inst_line}.\n"
                    "Якщо ви просили й інші установи, їхні рішення ще в дорозі: "
                    "про кожне напишемо окремо.\n\n"
                    f"Почати: {_site_url()}/uk/profile\n"
                )
            else:
                body = (
                    f"Вітаємо, {user.full_name}!\n\n"
                    f"Ваш запит підтверджено: {label}.\n\n"
                    "Вам доступні публічні локації. Доступ до інших територій "
                    "надають окремо їхні установи.\n\n"
                    f"Почати: {_site_url()}/uk/profile\n"
                )
        else:
            subject = "Запит на верифікацію відхилено — biomon"
            body = (
                f"Вітаємо, {user.full_name}!\n\n"
                f"Ваш запит відхилено: {label}.\n"
            )

    if note:
        body += ("\nAdministrator's note: " if lang == 'en' else "\nКоментар адміністратора: ")
        body += f"{note}\n"
    return send_email(subject, [user.email], body)


def send_rights_granted_email(user, modules):
    """Tell the user that verification rights were granted from the user form.

    Separate from :func:`send_decision_email` because the two are different
    events: that one answers a request the person filed ("your request was
    approved"), this one announces rights an administrator granted directly,
    which the recipient may never have asked for. One letter lists every module
    granted in the same save.
    """
    lang = _lang(user)
    labels = ', '.join(_MODULE_LABEL[m][lang] for m in modules if m in _MODULE_LABEL)
    if not labels:
        return False

    if lang == 'en':
        subject = "Verification rights granted — biomon"
        body = (
            f"Hello, {user.full_name}!\n\n"
            f"An administrator has granted you verification rights: {labels}.\n\n"
            "Access to a territory's data is granted separately by its "
            "institution.\n\n"
            f"Start here: {_site_url()}/en/profile\n"
        )
    else:
        subject = "Права верифікації надано — biomon"
        body = (
            f"Вітаємо, {user.full_name}!\n\n"
            f"Адміністратор надав вам права верифікації: {labels}.\n\n"
            "Доступ до даних конкретної території надають окремо її установи.\n\n"
            f"Почати: {_site_url()}/uk/profile\n"
        )
    return send_email(subject, [user.email], body)


def notify_admin_new_requests(user, modules):
    """Ping the admin (Telegram + email) about a confirmed registration.

    Called only AFTER the address is confirmed, so unconfirmed bot signups never
    reach the admin's inbox. Managers of the institutions the applicant named are
    notified too — the request waits in their queue, and nobody else can answer
    for their territory.
    """
    from app.utils.notifications import CH_BIOMON, send_notification
    from markupsafe import escape

    labels = ', '.join(_MODULE_LABEL[m]['uk'] for m in modules)
    institutions = sorted({inst.name_uk
                           for req in user.verification_requests
                           for inst in req.requested_institutions})
    inst_labels = ', '.join(institutions) or '—'
    # The same text sits on every module request of one signup; take the first
    # that has it.
    applicant_note = next((req.applicant_note for req in user.verification_requests
                           if req.applicant_note), None)

    telegram_note = (f"<b>Про себе:</b> {escape(applicant_note[:600])}\n"
                     if applicant_note else "")
    send_notification(
        "🙋 <b>Новий запит на верифікацію — biomon.app</b>\n\n"
        f"<b>Користувач:</b> {escape(user.full_name)} ({escape(user.username)})\n"
        f"<b>Email:</b> {escape(user.email)}\n"
        f"<b>Просить:</b> {escape(labels)}\n"
        f"<b>Установи:</b> {escape(inst_labels)}\n"
        f"{telegram_note}\n"
        f"{_site_url()}/uk/admin/verification-requests",
        channel=CH_BIOMON,
    )

    email_note = (f"\nПро себе та досвід:\n{applicant_note}\n"
                  if applicant_note else "")
    body = (
        f"Користувач: {user.full_name} ({user.username})\n"
        f"Email: {user.email}\n"
        f"Просить: {labels}\n"
        f"Установи: {inst_labels}\n"
        f"{email_note}\n"
        f"{_site_url()}/uk/admin/verification-requests\n"
    )
    admin_email = current_app.config.get('ADMIN_EMAIL')
    if admin_email:
        send_email("Новий запит на верифікацію — biomon", [admin_email], body)

    for manager_email in _managers_to_notify(user, admin_email):
        send_email("Новий запит на верифікацію — biomon", [manager_email], body)


def _managers_to_notify(user, admin_email=None):
    """Addresses of managers who can act on this applicant's request.

    A manager qualifies by holding the ``manager`` role and having access to one
    of the institutions named in the request. Admins are excluded: they already
    got the letter above.
    """
    institution_ids = {inst.id
                       for req in user.verification_requests
                       for inst in req.requested_institutions}
    if not institution_ids:
        return []

    from app.models import User as UserModel

    seen = set()
    if admin_email:
        seen.add(admin_email.lower())
    addresses = []
    for candidate in UserModel.query.filter(UserModel.is_active.is_(True)).all():
        if not candidate.email or candidate.has_role('admin'):
            continue
        if not any(role.name == 'manager' for role in candidate.roles):
            continue
        if not institution_ids & {inst.id for inst in candidate.institutions}:
            continue
        key = candidate.email.lower()
        if key in seen:
            continue
        seen.add(key)
        addresses.append(candidate.email)
    return addresses
