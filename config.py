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
    # Fail-fast: без SECRET_KEY у середовищі додаток НЕ стартує (KeyError).
    # Прибрано fallback на відомий ключ (SEC-002): якщо .env не завантажиться,
    # краще явна аварія, ніж тихий старт із ключем, відомим із git-історії.
    SECRET_KEY = os.environ['SECRET_KEY']

    # Налаштування бази даних
    # Беремо DATABASE_URL з .env. Якщо його там немає, створюємо локальну app.db.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'app.db')
    
    PAM_DATABASE_URI = os.environ.get('PAM_DATABASE_URL')
    CT_DATABASE_URI = os.environ.get('CT_DATABASE_URL')
    GEODATA_DATABASE_URI = os.environ.get('GEODATA_DATABASE_URI')
    # SDM (Species Distribution Models) модуль — окрема БД sdm_db,
    # спільна для camera_traps і PAM. Код у shared-sdm/ пакеті.
    SDM_DATABASE_URI = os.environ.get('SDM_DATABASE_URL')

    # Google Earth Engine (для SDM-предикторів: DEM, land cover, NDVI, ...).
    # Service account ключ (JSON) — той самий, що використовує myproject.
    GEE_SERVICE_ACCOUNT_KEY = os.environ.get('GEE_SERVICE_ACCOUNT_KEY')
    GEE_PROJECT_ID = os.environ.get('GEE_PROJECT_ID', 'yurastrus')

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # === НАЛАШТУВАННЯ ДЛЯ МОДУЛЯ CAMERA TRAPS ===
    CAMERA_TRAP_CONFIG = {
        'MAX_FILE_SIZE': 10 * 1024 * 1024,  # 10MB
        'THUMBNAIL_SIZE': (960, 960),
        'SERIES_TIME_WINDOW': 60,  # секунди для групування в серію
        'ALLOWED_EXTENSIONS': {'jpg', 'jpeg'},
        'MIN_IDENTIFICATIONS': 2,  # мінімум ідентифікацій для консенсусу
        'CLEANUP_DAYS': 0,  # дні після яких видаляти фото
        'STALE_BATCH_HOURS': 0,  # поріг віку для cleanup невдалих batchʼів (0=одразу; активні захищені probe)
        'ACTIVE_PROBE_SECONDS': 10,  # вікно спостереження за processed_files для виявлення активних batchʼів
        'CLEANUP_LOG_RETENTION_DAYS': 90,  # скільки тримати записи cleanup_log; видаляються при analyze

        # ── Календар покриття (#38) ──
        # Допустима прогалина (днів) між сусідніми датами з фото у fallback-режимі
        # (для локацій/деплойментів без дат start/end): прогалина ≤ N вважається
        # неперервним покриттям (камера стояла, тварини просто не проходили).
        'COVERAGE_MAX_GAP_DAYS': 15,
        # Поріг «добре» (зелений) у к-сті фото за добу для градації/легенди.
        'COVERAGE_GOOD_PHOTOS': 1,

        # ── Дефолтний scope фільтра «Установа / Екорегіон» (#49) ──
        # Який scope застосовується, коли в URL немає ?scope=... :
        #   ''                   → жодна установа не вибрана = «усі доступні дані» (дефолт);
        #   'ecoregion:Розточчя' → дефолтом стає екорегіон Розточчя (працює лише якщо
        #                          цей екорегіон доступний користувачу; інакше — «усі»);
        #   'institution:<id>'   → конкретна установа.
        # Резолвиться в resolve_scope() (dashboard, contributors). Один рядок —
        # один важіль на майбутнє; ЗА ЗАМОВЧУВАННЯМ нічого не нав'язує.
        'CT_DEFAULT_SCOPE': '',

        # ── AI-runner (DeepFaune або інший класифікатор) ──
        # Окремий процес з власним venv. Flask лише читає прогнози з
        # ai_predictions і пише завдання в ai_run_queue (для адмін-кнопки).
        # Усі ключі тут можна перевизначити через відповідні AI_RUNNER_*
        # змінні в .env (див. fields нижче).
        'AI_RUNNER': {
            # Глобальний feature-flag. Якщо False — фільтр на /identify
            # сірий, кнопка на /admin не показується. На локальній dev-машині
            # без worker'а можна виставити False у .env.
            'ENABLED':       os.environ.get('AI_RUNNER_ENABLED', 'true').lower() in ['true', '1', 'on'],

            # Скільки observation worker оброблятиме за один прогін.
            # Стосується і нічного cron'у, і ручної кнопки (як default).
            'MAX_PER_RUN':   int(os.environ.get('AI_RUNNER_MAX_PER_RUN', '200')),

            # Поріг впевненості для prediction_label/prediction_score.
            # top1_label зберігається завжди — поріг впливає лише на те,
            # що піде в "сильний" прогноз (тобто чи буде непорожнім
            # prediction_species_id).
            'THRESHOLD':     float(os.environ.get('AI_RUNNER_THRESHOLD', '0.8')),

            # Шляхи на сервері, де живе worker (НЕ в /var/www/biomon!).
            # Тримаємо в /opt щоб venv з torch не мішався з основним.
            'WORKER_PYTHON': os.environ.get('AI_RUNNER_WORKER_PYTHON', '/opt/biomon-ai/venv/bin/python'),
            'WORKER_PATH':   os.environ.get('AI_RUNNER_WORKER_PATH',   '/opt/biomon-ai'),

            # Яку модель worker зараз використовує (потрапить у ai_models).
            # Зміниш у майбутньому — і всі нові прогнози будуть під новим model_id.
            # Старі прогнози від попередньої моделі лишаються в БД для метрик.
            'MODEL_NAME':    os.environ.get('AI_RUNNER_MODEL_NAME',    'DeepFaune'),
            'MODEL_VERSION': os.environ.get('AI_RUNNER_MODEL_VERSION', '1.4.1'),
        },
    }

    PAM_MAX_UPLOAD_SIZE = 1000 * 1024 * 1024  # 500 MB для ZIP архівів
    PAM_ALLOWED_AUDIO_EXTENSIONS = {'.wav'}
    # ліміт кількості виділів які будуть показані на карті переглядача даних лісовпорядкування.
    
    # Налаштування пошти
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

    # Публічна URL-адреса сайту (для посилань у листах)
    SITE_URL = os.environ.get('SITE_URL', 'http://91.99.138.240:82')

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
    GEOSERVER_URL = '/geoserver'
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
    'default': ProductionConfig
}