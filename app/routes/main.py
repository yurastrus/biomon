# ==============================================
# app/routes/main.py - Спрощені маршрути
# ==============================================
from flask import render_template, session, redirect, url_for, current_app, request, g, jsonify, flash
from flask_login import login_required, current_user, login_user, logout_user
from app.utils.forms import LoginForm, ContactForm
from app.utils.utils import is_safe_url
from flask_babel import lazy_gettext as _l
from app.routes import bp
from app.models import User, SiteTextContent
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
    """Головна сторінка з динамічним контентом"""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    
    # Отримуємо контент для головної сторінки (page_key='home')
    content = SiteTextContent.query.filter_by(page_key='home').first()
    
    return render_template('index.html', 
                           lang_code=lang_code, 
                           content=content)

@bp.route('/<lang_code>/about')
def about(lang_code):
    """Сторінка про проект з даними з БД"""
    # Перевірка підтримуваних мов
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    
    # Отримуємо контент із нашої нової таблиці
    content = SiteTextContent.query.filter_by(page_key='about').first()
    
    # Якщо в базі ще немає тексту, передаємо None або пустий словник
    return render_template('about.html', 
                           lang_code=lang_code, 
                           content=content)

@bp.route('/<lang_code>/contacts')
def contacts(lang_code):
    """Сторінка контактів"""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    return render_template('contacts.html')

@bp.route('/<lang_code>/login', methods=['GET', 'POST'])
def login(lang_code):
    if current_user.is_authenticated: 
        return redirect(url_for('main.index', lang_code=g.lang_code))
    
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