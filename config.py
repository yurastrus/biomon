# SPDX-License-Identifier: AGPL-3.0-only
import os
from datetime import timedelta
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))

load_dotenv(os.path.join(basedir, '.env'))

class Config:
    """Base application configuration."""
    # Fail-fast: missing SECRET_KEY raises KeyError on startup.
    # Fallback to a known key removed (SEC-002): better an explicit crash than
    # a silent start with a key that is visible in git history.
    SECRET_KEY = os.environ['SECRET_KEY']

    # Falls back to a local app.db if DATABASE_URL is not set.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'app.db')
    
    PAM_DATABASE_URI = os.environ.get('PAM_DATABASE_URL')
    CT_DATABASE_URI = os.environ.get('CT_DATABASE_URL')
    GEODATA_DATABASE_URI = os.environ.get('GEODATA_DATABASE_URI')
    # SDM (Species Distribution Models) — separate sdm_db shared by camera_traps and PAM.
    SDM_DATABASE_URI = os.environ.get('SDM_DATABASE_URL')

    # Google Earth Engine for SDM predictors (DEM, land cover, NDVI, …).
    # Service-account key (JSON) — shared with myproject.
    GEE_SERVICE_ACCOUNT_KEY = os.environ.get('GEE_SERVICE_ACCOUNT_KEY')
    GEE_PROJECT_ID = os.environ.get('GEE_PROJECT_ID')

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # Camera traps module configuration
    CAMERA_TRAP_CONFIG = {
        'MAX_FILE_SIZE': 10 * 1024 * 1024,  # 10MB
        'THUMBNAIL_SIZE': (960, 960),
        'SERIES_TIME_WINDOW': 60,  # seconds; grouping window for series detection
        'ALLOWED_EXTENSIONS': {'jpg', 'jpeg'},
        'MIN_IDENTIFICATIONS': 2,  # minimum identifications required for consensus
        'CLEANUP_DAYS': 0,  # days after which to delete photos (0 = disabled)
        'STALE_BATCH_HOURS': 0,  # age threshold for cleaning up failed batches (0 = immediately; active batches protected by probe)
        'ACTIVE_PROBE_SECONDS': 10,  # observation window for processed_files to detect active batches
        'CLEANUP_LOG_RETENTION_DAYS': 90,  # days to retain cleanup_log entries; pruned on analyze

        # ── Coverage calendar (#38) ──
        # Max gap (days) between adjacent photo dates in fallback mode
        # (for locations/deployments without start/end dates): gap ≤ N is treated as
        # continuous coverage (camera was up; animals simply weren't passing by).
        'COVERAGE_MAX_GAP_DAYS': 15,
        # "Good" threshold (green) in daily photo count for the coverage gradient/legend.
        'COVERAGE_GOOD_PHOTOS': 1,

        # ── Default scope for the Institution / Ecoregion filter (#49) ──
        # Applied when the URL has no ?scope=... :
        #   ''                   → no institution pre-selected = all accessible data (default);
        #   'ecoregion:Roztochia' → default ecoregion (only when accessible to the user; otherwise all);
        #   'institution:<id>'   → specific institution.
        # Resolved in resolve_scope() (dashboard, contributors).
        # Default value imposes nothing.
        'CT_DEFAULT_SCOPE': '',

        # Max aggregation window (minutes) for CT export «location + independence interval» (#36).
        # Raise above 60 if needed; both UI and backend validate against this value.
        'EXPORT_MAX_AGG_MINUTES': 60,

        # ── AI runner (DeepFaune or other classifier) ──
        # Separate process with its own venv. Flask only reads predictions from
        # ai_predictions and writes jobs to ai_run_queue (for the admin button).
        # All keys here can be overridden via the corresponding AI_RUNNER_*
        # env vars in .env.
        'AI_RUNNER': {
            # Global feature flag. False: /identify filter is greyed out,
            # admin button hidden. Set False in .env on dev machines without a worker.
            'ENABLED':       os.environ.get('AI_RUNNER_ENABLED', 'true').lower() in ['true', '1', 'on'],

            # Observations processed per run (nightly cron and manual admin button).
            'MAX_PER_RUN':   int(os.environ.get('AI_RUNNER_MAX_PER_RUN', '200')),

            # Confidence threshold for prediction_label/prediction_score.
            # top1_label is always stored; threshold determines whether prediction_species_id is set.
            'THRESHOLD':     float(os.environ.get('AI_RUNNER_THRESHOLD', '0.8')),

            # Server paths for the worker (outside /var/www/biomon).
            # Kept in /opt to isolate the torch venv from the main one.
            'WORKER_PYTHON': os.environ.get('AI_RUNNER_WORKER_PYTHON', '/opt/biomon-ai/venv/bin/python'),
            'WORKER_PATH':   os.environ.get('AI_RUNNER_WORKER_PATH',   '/opt/biomon-ai'),

            # Model currently used by the worker (written to ai_models).
            # Changing this causes new predictions to be tracked under the new model_id.
            # Old predictions from the previous model remain in DB for metrics.
            'MODEL_NAME':    os.environ.get('AI_RUNNER_MODEL_NAME',    'DeepFaune'),
            'MODEL_VERSION': os.environ.get('AI_RUNNER_MODEL_VERSION', '1.4.1'),
        },
    }

    PAM_MAX_UPLOAD_SIZE = 1000 * 1024 * 1024  # 500 MB for ZIP archives
    PAM_ALLOWED_AUDIO_EXTENSIONS = {'.wav'}

    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

    # Public site URL (used in email links)
    SITE_URL = os.environ.get('SITE_URL', 'http://localhost:5000')

    LANGUAGES = {'en': 'English', 'uk': 'Українська'}
    BABEL_DEFAULT_LOCALE = 'uk'
    BABEL_DEFAULT_TIMEZONE = 'Europe/Kiev'
    BABEL_TRANSLATION_DIRECTORIES = os.path.join(basedir, 'translations')

    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Password policy (#27): minimum length for NEW passwords (admin create/edit forms).
    # Complexity (letters+digits) is enforced by form validators.
    PASSWORD_MIN_LENGTH = 8

    @staticmethod
    def init_app(app):
        """No-op hook for environment-specific setup."""
        pass

class DevelopmentConfig(Config):
    DEBUG = True
    GEOSERVER_URL = os.environ.get('GEOSERVER_URL') or 'http://localhost:8080/geoserver'
    CAMERA_TRAP_CONFIG = Config.CAMERA_TRAP_CONFIG.copy()
    CAMERA_TRAP_CONFIG.update({
        'UPLOAD_PATH': os.path.join(basedir, 'camera_trap_data')
    })
    PAM_UPLOAD_PATH = os.path.join(basedir, 'pam_data_import/segments')

class ProductionConfig(Config):
    DEBUG = False
    # SEC Phase 3 (#25): session cookies over HTTPS only — prevents session
    # hijacking on open Wi-Fi. biomon.app uses HTTPS (nginx HTTP→HTTPS redirect).
    # Base Config leaves SECURE=False for dev/test over plain HTTP.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Lax'   # explicit (inherited from Config)
    REMEMBER_COOKIE_SECURE = True     # for Flask-Login remember-me cookies
    GEOSERVER_URL = '/geoserver'
    CAMERA_TRAP_CONFIG = Config.CAMERA_TRAP_CONFIG.copy()
    CAMERA_TRAP_CONFIG.update({
        'UPLOAD_PATH': os.environ.get('CAMERA_TRAP_UPLOAD_PATH')
    })
    PAM_UPLOAD_PATH = os.environ.get('PAM_UPLOAD_PATH')


class TestingConfig(Config):
    """Configuration for automated tests."""
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': ProductionConfig
}