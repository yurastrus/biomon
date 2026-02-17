# ==============================================
# app/__init__.py
# ==============================================
from flask import Flask
from config import config
import os

def create_app(config_name=None):
    """Application factory для створення додатку"""
    if config_name is None:
        config_name = os.environ.get('FLASK_CONFIG', 'default')
    
    app = Flask(__name__)
    
    app.config.from_object(config[config_name])
    
    app.jinja_env.add_extension('jinja2.ext.do')
    
    # Ініціалізація розширень
    from app.extensions import init_extensions, db
    init_extensions(app)
    
    # Реєстрація blueprints
    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)

    # Імпорт модуля ПАМ
    from app.pam import pam_bp
    app.register_blueprint(pam_bp)

    # Імпорт модуля фотопасток
    from app.camera_traps import camera_traps_bp
    app.register_blueprint(camera_traps_bp, url_prefix='/<lang_code>/camera-traps')
    
    return app