# SPDX-License-Identifier: AGPL-3.0-only
from flask_babel import Babel

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

babel = Babel()
db = SQLAlchemy()
migrate = Migrate()
bcrypt = Bcrypt()
login_manager = LoginManager()
csrf = CSRFProtect()
mail = Mail()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
talisman = Talisman()

@login_manager.unauthorized_handler
def unauthorized():
    from flask import g, request, redirect, url_for, flash
    lang_code = getattr(g, 'lang_code', 'uk')
    flash(login_manager.login_message, login_manager.login_message_category)
    return redirect(url_for('main.login', lang_code=lang_code, next=request.url))

def init_extensions(app):
    """Initialize all Flask extensions."""
    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)
    login_manager.login_message = "Будь ласка, увійдіть, щоб отримати доступ."
    login_manager.login_message_category = "warning"

    babel.init_app(app, locale_selector=get_locale_function)
    app.context_processor(inject_global_vars)
    app.before_request(before_request_handler)

def get_locale_function():
    """Return the active locale; called by Babel on every request."""
    from app.utils.i18n import select_locale
    return select_locale()

def inject_global_vars():
    """Inject common variables into every template context."""
    from app.utils.i18n import inject_global_vars as get_vars
    return get_vars()

def before_request_handler():
    """Set up per-request globals (lang_code, Babel refresh)."""
    from app.utils.i18n import before_request_handler as handler
    return handler()


