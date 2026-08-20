# SPDX-License-Identifier: AGPL-3.0-only
import json

from flask import render_template, session, redirect, url_for, current_app, request, g, jsonify, flash
from flask_login import login_required, current_user, login_user, logout_user
from app.utils.forms import (LoginForm, ContactForm, ChangePasswordForm,
                            ChangeUsernameForm, RegistrationForm,
                            ResendConfirmationForm, NotificationPrefsForm)
from app.utils.utils import is_safe_url
from flask_babel import lazy_gettext as _l
from app.routes import bp
from app.models import User, SiteTextContent, ContactSubmission, VerificationRequest
from app.utils.notifications import CH_BIOMON, send_notification
from app.utils import notification_prefs as notif_prefs
from app.utils import registration as reg
from app.utils.emails import (send_confirmation_email, notify_admin_new_requests)
from app.utils.tokens import generate_email_token, verify_email_token
from app.extensions import bcrypt, limiter, csrf, db
from markupsafe import escape
from werkzeug.security import check_password_hash


_DUMMY_HASH_CACHE = None


def _dummy_password_hash():
    """A throwaway bcrypt hash used to keep failed-login timing roughly constant
    when the username does not exist — otherwise the missing bcrypt comparison
    makes unknown-user responses measurably faster (username enumeration).
    Computed once, lazily (needs an app/bcrypt context)."""
    global _DUMMY_HASH_CACHE
    if _DUMMY_HASH_CACHE is None:
        _DUMMY_HASH_CACHE = bcrypt.generate_password_hash('not-a-real-account').decode('utf-8')
    return _DUMMY_HASH_CACHE


@bp.route('/')
def root():
    """Redirect to the homepage with language prefix."""
    from app.utils.i18n import select_locale
    lang_code = select_locale()
    return redirect(url_for('main.index', lang_code=lang_code))

@bp.route('/<lang_code>/')
def index(lang_code):
    """Render homepage with dynamic content from the database."""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))

    content = SiteTextContent.query.filter_by(page_key='home').first()
    
    return render_template('index.html', 
                           lang_code=lang_code, 
                           content=content)

@bp.route('/<lang_code>/about')
def about(lang_code):
    """Render the about page with content loaded from the database."""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))

    content = SiteTextContent.query.filter_by(page_key='about').first()

    return render_template('about.html',
                           lang_code=lang_code, 
                           content=content)

@bp.route('/<lang_code>/contacts', methods=['GET', 'POST'])
@limiter.limit("5/hour", methods=["POST"])
def contacts(lang_code):
    """Public contact form: save to DB and notify admin via Telegram.

    No personal email is exposed; the admin replies manually from the mailbox
    shown in the Telegram card and in the admin panel.
    """
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))

    form = ContactForm()
    if form.validate_on_submit():
        try:
            submission = ContactSubmission(
                name=form.name.data.strip(),
                email=form.email.data.strip(),
                subject=(form.subject.data or '').strip() or None,
                message=form.message.data.strip(),
                ip_address=request.remote_addr,
            )
            db.session.add(submission)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Contact form: DB save failed: {e}")
            flash(_l('Сталася помилка при відправці. Спробуйте пізніше.'), 'danger')
            return render_template('contacts.html', form=form)

        # Notifying is best-effort: the message is already safely in the DB.
        text = (
            "📨 <b>Нове звернення з biomon.app</b>\n\n"
            f"<b>Імʼя:</b> {escape(submission.name)}\n"
            f"<b>Email:</b> {escape(submission.email)}\n"
            f"<b>Тема:</b> {escape(submission.subject or '—')}\n\n"
            f"{escape(submission.message)}"
        )
        send_notification(text, channel=CH_BIOMON)

        flash(_l('Ваше повідомлення надіслано! Ми звʼяжемося з вами найближчим часом.'), 'success')
        # PRG pattern: redirect after POST to avoid duplicate submits on refresh.
        return redirect(url_for('main.contacts', lang_code=lang_code))

    return render_template('contacts.html', form=form)

@bp.route('/<lang_code>/login', methods=['GET', 'POST'])
@limiter.limit("5/minute", methods=["POST"])
def login(lang_code):
    if current_user.is_authenticated:
        return redirect(url_for('main.index', lang_code=g.lang_code))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            # Correct credentials, but the account may not be usable yet. Checked
            # here rather than relying on login_user()'s silent False so the
            # person is told which of the two situations they are in.
            if not user.is_active:
                if user.self_registered and not user.is_email_confirmed:
                    flash(_l('Підтвердіть, будь ласка, свою email-адресу. '
                             'Не отримали листа? Надішліть посилання ще раз.'), 'warning')
                    return redirect(url_for('main.resend_confirmation', lang_code=g.lang_code))
                flash(_l('Цей акаунт деактивовано. Зверніться до адміністратора.'), 'danger')
                return render_template('login.html', title=_l('Увійти'), form=form)
            session.clear()
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                return redirect(url_for('main.index', lang_code=g.lang_code))
            return redirect(next_page)
        else:
            if user is None:
                # Constant-ish timing: run a bcrypt comparison even for unknown
                # usernames so response time can't reveal which logins exist.
                bcrypt.check_password_hash(_dummy_password_hash(), form.password.data)
            current_app.logger.warning(
                f"Failed login: username={form.username.data!r} "
                f"from {request.remote_addr} UA={request.user_agent.string[:100]!r}"
            )
            flash(_l('Неправильний логін або пароль. Спробуйте ще раз.'), 'danger')

    return render_template('login.html', title=_l('Увійти'), form=form)

@bp.route('/<lang_code>/register', methods=['GET', 'POST'])
@limiter.limit("5/hour;20/day", methods=["POST"])
def register(lang_code):
    """Public self-service registration.

    Creates an INACTIVE account plus one pending verification request per module
    the person picked, and emails a confirmation link. Nothing is granted here:
    logging in needs the address confirmed, and verifying needs an
    administrator's approval (see app/utils/registration.py for the split).
    """
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    if current_user.is_authenticated:
        return redirect(url_for('main.profile', lang_code=g.lang_code))

    form = RegistrationForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()

        # Uniqueness is enforced in the DB too (unique index on user.email); this
        # check exists to produce a field-level message instead of a 500.
        if reg.username_taken(username):
            form.username.errors.append(_l('Це імʼя користувача вже зайняте.'))
        if reg.email_taken(email):
            form.email.errors.append(_l('Ця email-адреса вже зареєстрована.'))

        if not form.username.errors and not form.email.errors:
            try:
                user = reg.create_self_registered_user(
                    username=username,
                    email=email,
                    password=form.password.data,
                    first_name=form.first_name.data.strip(),
                    last_name=form.last_name.data.strip(),
                    modules=form.selected_modules,
                    locale=lang_code,
                )
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Registration failed for {email!r}: {e}")
                flash(_l('Не вдалося створити акаунт. Спробуйте пізніше.'), 'danger')
                return render_template('register.html', form=form)

            current_app.logger.info(
                "Self-registration: user_id=%s username=%r modules=%s from %s",
                user.id, user.username, form.selected_modules, request.remote_addr)
            send_confirmation_email(user, generate_email_token(user.email))

            flash(_l('Акаунт створено. Ми надіслали лист із посиланням для '
                     'підтвердження — перейдіть за ним, щоб активувати вхід.'), 'success')
            return redirect(url_for('main.login', lang_code=lang_code))

    return render_template('register.html', form=form)


@bp.route('/<lang_code>/confirm/<token>')
@limiter.limit("20/hour")
def confirm_email(lang_code, token):
    """Activate the account whose address this signed token proves.

    Idempotent by design: a second click says "already confirmed" instead of
    failing, because mail clients pre-fetch links.
    """
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))

    email = verify_email_token(token)
    if not email:
        flash(_l('Посилання недійсне або прострочене. Запросіть новий лист.'), 'danger')
        return redirect(url_for('main.resend_confirmation', lang_code=lang_code))

    user = User.query.filter(db.func.lower(User.email) == email.lower()).first()
    if user is None:
        # Address confirmed but the account is gone (purged as unconfirmed, or
        # deleted). Nothing to activate; do not hint at whether it ever existed.
        flash(_l('Посилання недійсне або прострочене. Запросіть новий лист.'), 'danger')
        return redirect(url_for('main.resend_confirmation', lang_code=lang_code))

    if not reg.confirm_email(user):
        flash(_l('Ця адреса вже підтверджена — можете входити.'), 'info')
        return redirect(url_for('main.login', lang_code=lang_code))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Email confirmation failed for user {user.id}: {e}")
        flash(_l('Сталася помилка. Спробуйте пізніше.'), 'danger')
        return redirect(url_for('main.login', lang_code=lang_code))

    current_app.logger.info("Email confirmed: user_id=%s", user.id)

    # The admin is notified only now — unconfirmed signups never reach the inbox.
    pending = [r.module for r in user.verification_requests
               if r.status == VerificationRequest.STATUS_PENDING]
    if pending:
        notify_admin_new_requests(user, pending)

    flash(_l('Адресу підтверджено. Тепер можете увійти. Запит на права '
             'верифікації надіслано адміністратору.'), 'success')
    return redirect(url_for('main.login', lang_code=lang_code))


@bp.route('/<lang_code>/resend-confirmation', methods=['GET', 'POST'])
@limiter.limit("3/hour;10/day", methods=["POST"])
def resend_confirmation(lang_code):
    """Re-send a confirmation link.

    Always reports success: telling the visitor whether an address is registered
    (or already confirmed) would turn this into an account-enumeration oracle.
    """
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))

    form = ResendConfirmationForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user is not None and user.self_registered and not user.is_email_confirmed:
            send_confirmation_email(user, generate_email_token(user.email))
            current_app.logger.info("Confirmation email re-sent: user_id=%s", user.id)
        else:
            current_app.logger.info(
                "Resend-confirmation requested for a non-eligible address from %s",
                request.remote_addr)
        flash(_l('Якщо ця адреса очікує підтвердження, ми надіслали лист ще раз.'),
              'info')
        return redirect(url_for('main.login', lang_code=lang_code))

    return render_template('resend_confirmation.html', form=form)


@bp.route('/<lang_code>/logout')
@login_required
def logout(lang_code):
    logout_user()
    return redirect(url_for('main.index', lang_code=lang_code))


@bp.route('/<lang_code>/profile', methods=['GET', 'POST'])
@login_required
@limiter.limit("10/hour", methods=["POST"])
def profile(lang_code):
    """Render the user profile page: password/username change forms and CT/PAM stats."""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))

    password_form = ChangePasswordForm()
    username_form = ChangeUsernameForm()
    notifications_form = NotificationPrefsForm()

    # Password change
    if password_form.submit_password.data and password_form.validate_on_submit():
        if not bcrypt.check_password_hash(current_user.password_hash,
                                          password_form.current_password.data):
            flash(_l('Поточний пароль невірний.'), 'danger')
        else:
            current_user.password_hash = bcrypt.generate_password_hash(
                password_form.new_password.data).decode('utf-8')
            db.session.commit()
            current_app.logger.info(f"Password changed by user_id={current_user.id}")
            flash(_l('Пароль успішно змінено.'), 'success')
            return redirect(url_for('main.profile', lang_code=lang_code))

    # Username change (user id is stable so no FK breakage; uniqueness validated here)
    if username_form.submit_username.data and username_form.validate_on_submit():
        new_username = (username_form.new_username.data or '').strip()
        if new_username == current_user.username:
            flash(_l('Це ваш поточний логін.'), 'info')
        elif User.query.filter(User.username == new_username,
                               User.id != current_user.id).first():
            flash(_l('Цей логін уже зайнятий.'), 'danger')
        else:
            old = current_user.username
            current_user.username = new_username
            db.session.commit()
            current_app.logger.info(
                f"Username changed: user_id={current_user.id} {old!r} -> {new_username!r}")
            flash(_l('Логін успішно змінено.'), 'success')
            return redirect(url_for('main.profile', lang_code=lang_code))

    # Email notification opt-outs. Everyone may edit their own; admins edit
    # other people's from the admin user form.
    if (notifications_form.submit_notifications.data
            and notifications_form.validate_on_submit()):
        changed = notif_prefs.apply_form(current_user, request.form)
        db.session.commit()
        if changed:
            current_app.logger.info(
                f"Notification prefs changed by user_id={current_user.id}: "
                f"{', '.join(changed)}")
        flash(_l('Налаштування сповіщень збережено.'), 'success')
        return redirect(url_for('main.profile', lang_code=lang_code))

    # Stats — read-only; module errors must not break the page
    ct_stats = pam_stats = None
    try:
        from app.camera_traps.utils import get_user_ct_stats
        ct_stats = get_user_ct_stats(current_user.id, lang=lang_code)
    except Exception as e:
        current_app.logger.warning(f"profile: CT stats unavailable: {e}")
    try:
        from app.pam.utils import get_user_pam_stats
        pam_stats = get_user_pam_stats(current_user.id, lang=lang_code)
    except Exception as e:
        current_app.logger.warning(f"profile: PAM stats unavailable: {e}")

    if not username_form.new_username.data:
        username_form.new_username.data = current_user.username

    return render_template('profile.html', lang_code=lang_code,
                           password_form=password_form,
                           username_form=username_form,
                           notifications_form=notifications_form,
                           notification_prefs=notif_prefs.NOTIFICATION_PREFS,
                           notification_field_prefix=notif_prefs.FIELD_PREFIX,
                           ct_stats=ct_stats, pam_stats=pam_stats,
                           verification_requests=sorted(
                               current_user.verification_requests,
                               key=lambda r: r.module))


@bp.route('/csp-report', methods=['POST'])
@csrf.exempt
@limiter.limit("100/hour")
def csp_report():
    """Receive and log CSP violation reports from browsers.

    No auth required — browsers do not attach cookies to violation reports (RFC).
    Rate-limited to guard against bot spam. CSRF exempt because browsers do not
    include CSRF tokens in auto-generated reports.
    """
    # Browsers send with Content-Type: application/csp-report (legacy)
    # or application/reports+json (modern, via report-to header).
    # force=True ignores the Content-Type check; silent=True catches malformed input.
    report = request.get_json(force=True, silent=True) or {}

    # Truncate to avoid log pollution from large payloads
    payload = json.dumps(report, ensure_ascii=False)[:2000]
    current_app.logger.warning(f"CSP violation: {payload}")

    # 204 No Content — standard for CSP report endpoints, consumes no bandwidth
    return '', 204