# ==============================================
# app/routes/main.py - Спрощені маршрути
# ==============================================
from flask import render_template, session, redirect, url_for, current_app, request, g, jsonify
from app.routes import bp

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

@bp.route('/<lang_code>/webmaps')
def webmaps(lang_code):
    """Сторінка веб-карт"""
    if lang_code not in current_app.config['LANGUAGES']:
        return redirect(url_for('main.root'))
    return render_template('webmaps.html')

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