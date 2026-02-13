# ==============================================
# app/utils/i18n.py - ФІНАЛЬНА ЧИСТА ВЕРСІЯ
# ==============================================
from flask import request, session, g, current_app
from flask_babel import gettext, ngettext, refresh
import datetime

def select_locale():
    """
    Функція вибору локалі - викликається Babel для кожного запиту
    """
    selected_lang = None
    
    # 1. Пріоритет: lang_code в URL
    if request.view_args and 'lang_code' in request.view_args:
        url_lang = request.view_args['lang_code']
        if url_lang in current_app.config['LANGUAGES']:
            selected_lang = url_lang
            # Зберігаємо в сесії для майбутніх запитів
            session['language'] = url_lang
    
    # 2. Якщо немає в URL - беремо з сесії
    elif 'language' in session and session['language'] in current_app.config['LANGUAGES']:
        selected_lang = session['language']
    
    # 3. По замовчуванню
    else:
        selected_lang = current_app.config.get('BABEL_DEFAULT_LOCALE', 'uk')
        session['language'] = selected_lang
    
    return selected_lang

def before_request_handler():
    """Handler перед кожним запитом"""
    # Встановлюємо поточну мову в g для використання в шаблонах
    g.lang_code = select_locale()
    
    # ВАЖЛИВО: Оновлюємо контекст Babel
    try:
        refresh()
    except Exception:
        pass  # Ігноруємо можливі помилки refresh

def inject_global_vars():
    """Context processor для шаблонів"""
    return {
        'select_locale': select_locale,
        '_': gettext,
        'ngettext': ngettext,
        'now': datetime.datetime.now(),
        'LANGUAGES': current_app.config['LANGUAGES']
    }