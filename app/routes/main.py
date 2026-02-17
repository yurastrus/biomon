# ==============================================
# app/routes/main.py - Спрощені маршрути
# ==============================================
from flask import render_template, session, redirect, url_for, current_app, request, g, jsonify, flash
from flask_login import login_required, current_user, login_user
from app.utils.forms import LoginForm, ContactForm
from app.utils.utils import is_safe_url
from flask_babel import lazy_gettext as _l
from app.routes import bp
from app.models import User
from app.extensions import bcrypt
from werkzeug.security import check_password_hash


@bp.route('/')
def root():
    """Перенаправлення на головну з мовою"""
    from app.utils.i18n import select_locale
    lang_code = select_locale()
    return redirect(url_for('main.index', lang_code=lang_code))

@bp.route('/<lang_code>/')
def index(lang_code):
    """Головна сторінка"""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    return render_template('index.html')

@bp.route('/<lang_code>/about')
def about(lang_code):
    """Сторінка про проект"""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    return render_template('about.html')

@bp.route('/<lang_code>/journal')
def journal(lang_code):
    """Сторінка журналу (тепер перенаправляє на новий blueprint)"""
    return redirect(url_for('journal.index', lang_code=lang_code))

@bp.route('/<lang_code>/contacts')
def contacts(lang_code):
    """Сторінка контактів"""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    return render_template('contacts.html')

@bp.route('/<lang_code>/login', methods=['GET', 'POST'])
def login(lang_code):
    if current_user.is_authenticated: 
        return redirect(url_for('main.homindex', lang_code=g.lang_code))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                return redirect(url_for('main.index', lang_code=g.lang_code))
            return redirect(next_page)
        else:
            flash(_l('Неправильний логін або пароль. Спробуйте ще раз.'), 'danger')
    
    return render_template('login.html', title=_l('Увійти'), form=form)

@bp.route('/<lang_code>/logout')
@login_required
def logout(lang_code):
    logout_user()
    return redirect(url_for('main.index', lang_code=lang_code))

@bp.route('/<lang_code>/contact', methods=['GET', 'POST'])
def contact_page(lang_code):
    form = ContactForm()
    # Якщо користувач не пройшов капчу, метод поверне False.
    if form.validate_on_submit():
        try:
            # 1. Зберігаємо повідомлення в БД
            msg = ContactMessage(
                name=form.name.data,
                email=form.email.data,
                subject=form.subject.data,
                message=form.message.data
            )
            db.session.add(msg)
            db.session.commit()

            # 2. Відправляємо email (бажано обгорнути в try-except, щоб помилка пошти не валила сайт)
            send_async_email(
                current_app._get_current_object(),
                form.name.data,
                form.email.data,
                form.subject.data,
                form.message.data
            )
            
            flash(_l('Ваше повідомлення було успішно надіслано! Ми зв\'яжемося з вами найближчим часом.'), 'success')
            # Використовуємо redirect, щоб уникнути повторної відправки форми при оновленні сторінки (PRG Pattern)
            return redirect(url_for('main.contact_page', lang_code=lang_code))
            
        except Exception as e:
            # Логуємо помилку, якщо щось пішло не так з БД або поштою
            db.session.rollback()
            current_app.logger.error(f"Error sending contact message: {e}")
            flash(_l('Сталася помилка при відправці повідомлення. Спробуйте пізніше.'), 'danger')
        
    # Якщо валідація не пройшла (в т.ч. помилка капчі), 
    # сторінка просто перезавантажиться з відображенням помилок.
    return render_template('contacts.html', title=_l('Зворотній зв\'язок'), form=form)