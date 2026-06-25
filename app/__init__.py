# SPDX-License-Identifier: AGPL-3.0-only
from flask import Flask
from config import config
import os


def _init_talisman(app):
    """Configure Flask-Talisman security headers and CSP policy (report-only).

    Extracted so tests can apply Talisman to a test app independently;
    skipped in create_app when TESTING=True.
    """
    from app.extensions import talisman, csrf
    talisman.init_app(
        app,
        content_security_policy={
            'default-src': "'self'",
            'img-src': ["'self'", 'data:', 'https:', 'blob:'],
            'script-src': [
                "'self'",
                "'unsafe-inline'",  # required for inline JS in templates
                'https://cdn.jsdelivr.net',
                'https://code.jquery.com',
                'https://cdn.plot.ly',
                'https://unpkg.com',
                'https://cdnjs.cloudflare.com',
            ],
            'style-src': [
                "'self'",
                "'unsafe-inline'",
                'https://cdn.jsdelivr.net',
                'https://unpkg.com',
            ],
            'font-src': ["'self'", 'https://cdn.jsdelivr.net', 'data:'],
            'connect-src': "'self'",
            'media-src': ["'self'", 'data:', 'blob:'],
        },
        content_security_policy_report_only=True,
        content_security_policy_report_uri='/csp-report',
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        session_cookie_secure=False,
        force_https=False,
    )


def create_app(config_name=None):
    """Application factory."""
    if config_name is None:
        config_name = os.environ.get('FLASK_CONFIG', 'default')

    app = Flask(__name__)

    app.config.from_object(config[config_name])

    app.jinja_env.add_extension('jinja2.ext.do')

    from app.extensions import init_extensions, db, limiter
    init_extensions(app)
    limiter.init_app(app)

    if not app.config.get('TESTING'):
        _init_talisman(app)
    
    # Register blueprints
    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    from app.admin import admin_bp
    app.register_blueprint(admin_bp)

    from app.pam import pam_bp
    app.register_blueprint(pam_bp)

    from app.camera_traps import camera_traps_bp
    app.register_blueprint(camera_traps_bp, url_prefix='/<lang_code>/camera-traps')

    from app.sdm import sdm_bp
    app.register_blueprint(sdm_bp, url_prefix='/<lang_code>/sdm')

    # SEO: /robots.txt and /sitemap.xml at the domain root (no lang prefix).
    from app.seo import seo_bp
    app.register_blueprint(seo_bp)

    from app.commands import register_commands
    register_commands(app)

    return app