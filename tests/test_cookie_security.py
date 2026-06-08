"""
SEC Phase 3 (#25): сесійні куки лише по HTTPS у Production.

ProductionConfig: SESSION_COOKIE_SECURE / REMEMBER_COOKIE_SECURE = True,
SAMESITE='Lax', HTTPONLY=True. Dev/base лишаються SECURE=False, щоб локальна
робота по HTTP не ламалась.
"""
from config import Config, DevelopmentConfig, ProductionConfig


def test_production_session_cookie_secure():
    assert ProductionConfig.SESSION_COOKIE_SECURE is True


def test_production_remember_cookie_secure():
    assert ProductionConfig.REMEMBER_COOKIE_SECURE is True


def test_production_samesite_lax():
    assert ProductionConfig.SESSION_COOKIE_SAMESITE == 'Lax'


def test_production_httponly_true():
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True


def test_development_session_cookie_not_secure():
    """Dev має лишатися SECURE=False — інакше зламає локальні HTTP-сесії."""
    assert DevelopmentConfig.SESSION_COOKIE_SECURE is False


def test_base_config_secure_false():
    assert Config.SESSION_COOKIE_SECURE is False
