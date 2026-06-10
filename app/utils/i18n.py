from flask import request, session, g, current_app
from flask_babel import gettext, ngettext, refresh
import datetime

def select_locale():
    """Return the active locale; called by Babel on every request.

    Priority: URL lang_code → session → application default.
    """
    selected_lang = None

    # Priority 1: lang_code from URL
    if request.view_args and 'lang_code' in request.view_args:
        url_lang = request.view_args['lang_code']
        if url_lang in current_app.config['LANGUAGES']:
            selected_lang = url_lang
            session['language'] = url_lang

    # Priority 2: language stored in session
    elif 'language' in session and session['language'] in current_app.config['LANGUAGES']:
        selected_lang = session['language']

    # Priority 3: application default
    else:
        selected_lang = current_app.config.get('BABEL_DEFAULT_LOCALE', 'uk')
        session['language'] = selected_lang

    return selected_lang

def before_request_handler():
    """Set g.lang_code and refresh Babel translation context before each request."""
    g.lang_code = select_locale()

    try:
        refresh()
    except Exception:
        pass  # refresh() is best-effort

def inject_global_vars():
    """Inject i18n helpers and common config into every template context."""
    return {
        'select_locale': select_locale,
        '_': gettext,
        'ngettext': ngettext,
        'now': datetime.datetime.now(),
        'LANGUAGES': current_app.config['LANGUAGES']
    }