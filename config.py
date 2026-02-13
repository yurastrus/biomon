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
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
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
    """Конфігурація для розробки"""
    DEBUG = True
    ENV = 'development'

class ProductionConfig(Config):
    """Конфігурація для продакшену"""
    DEBUG = False
    ENV = 'production'
    SESSION_COOKIE_SECURE = True  # Тільки для HTTPS

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