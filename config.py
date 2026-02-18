# ==============================================
# /config.py
# ==============================================
import os
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))

load_dotenv(os.path.join(basedir, '.env'))

class Config:
    """Базова конфігурація додатку"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-very-secret-string-for-development'

    # Налаштування бази даних
    # Беремо DATABASE_URL з .env. Якщо його там немає, створюємо локальну app.db.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'app.db')
    
    PAM_DATABASE_URI = os.environ.get('PAM_DATABASE_URL')
    CT_DATABASE_URI = os.environ.get('CT_DATABASE_URL')
    GEODATA_DATABASE_URI = os.environ.get('GEODATA_DATABASE_URI') 

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # === НАЛАШТУВАННЯ ДЛЯ МОДУЛЯ CAMERA TRAPS ===
    CAMERA_TRAP_CONFIG = {
        'MAX_FILE_SIZE': 10 * 1024 * 1024,  # 10MB
        'THUMBNAIL_SIZE': (800, 800),
        'SERIES_TIME_WINDOW': 60,  # секунди для групування в серію
        'ALLOWED_EXTENSIONS': {'jpg', 'jpeg'},
        'MIN_IDENTIFICATIONS': 2,  # мінімум ідентифікацій для консенсусу
        'CLEANUP_DAYS': 0,  # дні після яких видаляти фото
        'STALE_BATCH_HOURS': 0
    }

    PAM_MAX_UPLOAD_SIZE = 1000 * 1024 * 1024  # 500 MB для ZIP архівів
    PAM_ALLOWED_AUDIO_EXTENSIONS = {'.wav'}
    # ліміт кількості виділів які будуть показані на карті переглядача даних лісовпорядкування.
    
    # Налаштування мов
    LANGUAGES = {'en': 'English', 'uk': 'Українська'}
    BABEL_DEFAULT_LOCALE = 'uk'
    BABEL_DEFAULT_TIMEZONE = 'Europe/Kiev'
    BABEL_TRANSLATION_DIRECTORIES = os.path.join(basedir, 'translations')
    
    # Налаштування сесії
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    @staticmethod
    def init_app(app):
        """Додаткова ініціалізація, якщо потрібна"""
        pass

class DevelopmentConfig(Config):
    DEBUG = True
    GEOSERVER_URL = os.environ.get('GEOSERVER_URL') or 'http://91.99.138.240:8080/geoserver'
    CAMERA_TRAP_CONFIG = Config.CAMERA_TRAP_CONFIG.copy()
    CAMERA_TRAP_CONFIG.update({
        'UPLOAD_PATH': os.path.join(basedir, 'camera_trap_data')
    })
    PAM_UPLOAD_PATH = os.path.join(basedir, 'pam_data_import/segments')

class ProductionConfig(Config):
    DEBUG = False
    GEOSERVER_URL = os.environ.get('GEOSERVER_URL') or '/geoserver'
    CAMERA_TRAP_CONFIG = Config.CAMERA_TRAP_CONFIG.copy()
    CAMERA_TRAP_CONFIG.update({
        'UPLOAD_PATH': os.environ.get('CAMERA_TRAP_UPLOAD_PATH')
    })
    PAM_UPLOAD_PATH = os.environ.get('PAM_UPLOAD_PATH')


class TestingConfig(Config):
    """Конфігурація для тестування"""
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False

# Словник доступних конфігурацій
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}