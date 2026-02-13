# ==============================================
# app/__init__.py
# ==============================================
from flask import Flask
from config import Config

def create_app(config_class=Config):
    """Application factory для створення додатку"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    app.jinja_env.add_extension('jinja2.ext.do')
    
    # Ініціалізація розширень
    from app.extensions import init_extensions, db
    init_extensions(app)
    
    # Реєстрація blueprints
    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)

    from app.journal import journal_bp
    app.register_blueprint(journal_bp)

    # Імпортуємо моделі, щоб вони були видимі для Flask-Migrate
    from app.models import journal_models
    
    return app