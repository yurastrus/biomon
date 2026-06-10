import json

from flask import render_template, session, redirect, url_for, current_app, request, g, jsonify, flash
from flask_login import login_required, current_user, login_user, logout_user
from app.utils.forms import LoginForm, ContactForm, ChangePasswordForm, ChangeUsernameForm
from app.utils.utils import is_safe_url
from flask_babel import lazy_gettext as _l
from app.routes import bp
from app.models import User, SiteTextContent
from app.extensions import bcrypt, limiter, csrf, db
from werkzeug.security import check_password_hash


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

@bp.route('/<lang_code>/contacts')
def contacts(lang_code):
    """Render the contacts page."""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    return render_template('contacts.html')

@bp.route('/<lang_code>/login', methods=['GET', 'POST'])
@limiter.limit("5/minute", methods=["POST"])
def login(lang_code):
    if current_user.is_authenticated:
        return redirect(url_for('main.index', lang_code=g.lang_code))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            session.clear()
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                return redirect(url_for('main.index', lang_code=g.lang_code))
            return redirect(next_page)
        else:
            current_app.logger.warning(
                f"Failed login: username={form.username.data!r} "
                f"from {request.remote_addr} UA={request.user_agent.string[:100]!r}"
            )
            flash(_l('Неправильний логін або пароль. Спробуйте ще раз.'), 'danger')

    return render_template('login.html', title=_l('Увійти'), form=form)

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

    # Stats — read-only; module errors must not break the page
    ct_stats = pam_stats = None
    try:
        from app.camera_traps.utils import get_user_ct_stats
        ct_stats = get_user_ct_stats(current_user.id, lang=lang_code)
    except Exception as e:
        current_app.logger.warning(f"profile: CT-статистика недоступна: {e}")
    try:
        from app.pam.utils import get_user_pam_stats
        pam_stats = get_user_pam_stats(current_user.id)
    except Exception as e:
        current_app.logger.warning(f"profile: PAM-статистика недоступна: {e}")

    if not username_form.new_username.data:
        username_form.new_username.data = current_user.username

    return render_template('profile.html', lang_code=lang_code,
                           password_form=password_form,
                           username_form=username_form,
                           ct_stats=ct_stats, pam_stats=pam_stats)


@bp.route('/csp-report', methods=['POST'])
@csrf.exempt
@limiter.limit("100/hour")
def csp_report():
    """Receive and log CSP violation reports from browsers.

    No auth required — browsers do not attach cookies to violation reports (RFC).
    Rate-limited to guard against bot spam. CSRF exempt because browsers do not
    include CSRF tokens in auto-generated reports.
    """
    # Браузери надсилають з Content-Type: application/csp-report (legacy)
    # або application/reports+json (modern, через report-to header).
    # force=True ігнорує Content-Type check; silent=True ловить malformed.
    report = request.get_json(force=True, silent=True) or {}

    # Truncate щоб уникнути ушкодження логу від великих payload-ів
    payload = json.dumps(report, ensure_ascii=False)[:2000]
    current_app.logger.warning(f"CSP violation: {payload}")

    # 204 No Content — стандарт для CSP report endpoints, не споживає bandwidth
    return '', 204