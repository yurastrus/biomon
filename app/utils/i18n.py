# SPDX-License-Identifier: AGPL-3.0-only
from flask import request, session, g, current_app
from flask_babel import gettext, ngettext, refresh
import datetime

def select_locale():
    """Return the active locale; called by Babel on every request.

    Priority: URL lang_code → g.lang_code → session → application default.

    The g.lang_code step is what makes the admin panel switch languages: the
    admin blueprint's url_value_preprocessor pops `lang_code` out of
    request.view_args (so admin views don't need a lang_code parameter) and
    stores it in g.lang_code *before* this selector runs. Without reading
    g.lang_code we would never see the URL language for /<lang>/admin/... and
    would fall back to the session/default (the i18n bug this fixes).
    """
    languages = current_app.config['LANGUAGES']
    selected_lang = None

    # Priority 1: lang_code from the URL view args (normal blueprints keep it there).
    if request.view_args and 'lang_code' in request.view_args:
        url_lang = request.view_args['lang_code']
        if url_lang in languages:
            selected_lang = url_lang

    # Priority 2: lang_code already extracted into g by a blueprint
    # url_value_preprocessor that popped it out of view_args (e.g. admin).
    if selected_lang is None:
        g_lang = getattr(g, 'lang_code', None)
        if g_lang in languages:
            selected_lang = g_lang

    # Priority 3: language stored in session.
    if selected_lang is None and session.get('language') in languages:
        selected_lang = session['language']

    # Priority 4: application default.
    if selected_lang is None:
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