# ==============================================
# app/extensions.py
# ==============================================
from flask_babel import Babel

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
#from flask_bcrypt import Bcrypt
#from flask_login import LoginManager
#from flask_mail import Mail

babel = Babel()
db = SQLAlchemy()
migrate = Migrate()
#bcrypt = Bcrypt()
#login_manager = LoginManager()
#mail = Mail()

def init_extensions(app):
    """Ініціалізація всіх розширень Flask"""

     # Ініціалізація бази даних та міграцій
    db.init_app(app)
    migrate.init_app(app, db)
    
    # ВАЖЛИВО: Ініціалізація Babel з правильними налаштуваннями
    babel.init_app(app, locale_selector=get_locale_function)
    
    # Реєстрація context processor для глобальних змінних
    app.context_processor(inject_global_vars)
    
    # Реєстрація before_request handler
    app.before_request(before_request_handler)

def get_locale_function():
    """Функція для Babel - має бути доступна на рівні модуля"""
    from app.utils.i18n import select_locale
    return select_locale()

def inject_global_vars():
    """Context processor для шаблонів"""
    from app.utils.i18n import inject_global_vars as get_vars
    return get_vars()

def before_request_handler():
    """Before request handler"""
    from app.utils.i18n import before_request_handler as handler
    return handler()

