"""SEC-010: тести для Flask-Talisman security headers + CSP report endpoint.

Talisman у `create_app` пропускається коли TESTING=True (щоб не ламати
існуючі тести). Тому тут створюємо власний модульний `app` фікстуру
з ручною ініціалізацією Talisman через `_init_talisman`.
"""
import logging

import pytest


@pytest.fixture(scope='module')
def talisman_app(_ct_engine_patch):
    """Окрема Flask-app instance з активованим Talisman."""
    from app import create_app, _init_talisman
    from app.extensions import db

    flask_app = create_app('testing')
    flask_app.config['WTF_CSRF_ENABLED'] = False
    # Talisman пропустився в create_app (TESTING=True) — застосовуємо вручну
    _init_talisman(flask_app)
    with flask_app.app_context():
        db.create_all()
    return flask_app


@pytest.fixture(scope='module')
def talisman_client(talisman_app):
    return talisman_app.test_client()


# ── Security headers (Talisman defaults + CSP) ──────────────────────────────

def test_csp_report_only_header_present(talisman_client):
    """Talisman повертає Content-Security-Policy-Report-Only, не enforce."""
    resp = talisman_client.get('/')
    # У режимі report-only заголовок саме `...-Report-Only`, а не `Content-Security-Policy`
    assert 'Content-Security-Policy-Report-Only' in resp.headers
    csp = resp.headers['Content-Security-Policy-Report-Only']
    assert "default-src 'self'" in csp
    assert '/csp-report' in csp


def test_strict_transport_security_present(talisman_client):
    # HSTS видається тільки над HTTPS — імітуємо TLS-запит
    resp = talisman_client.get('/', base_url='https://localhost')
    assert 'Strict-Transport-Security' in resp.headers
    assert 'max-age=31536000' in resp.headers['Strict-Transport-Security']


def test_x_frame_options_present(talisman_client):
    resp = talisman_client.get('/')
    # Talisman default = SAMEORIGIN
    assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'


def test_x_content_type_options_nosniff(talisman_client):
    resp = talisman_client.get('/')
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'


# ── /csp-report endpoint ────────────────────────────────────────────────────

def test_csp_report_endpoint_accepts_json(talisman_client, caplog):
    payload = {
        "csp-report": {
            "document-uri": "http://localhost/uk/",
            "blocked-uri": "https://evil.example.com/script.js",
            "violated-directive": "script-src",
        }
    }
    with caplog.at_level(logging.WARNING):
        resp = talisman_client.post(
            '/csp-report',
            json=payload,
            content_type='application/csp-report',
        )
    assert resp.status_code == 204
    assert resp.data == b''
    assert any('CSP violation' in rec.message for rec in caplog.records)
    assert any('evil.example.com' in rec.message for rec in caplog.records)


def test_csp_report_endpoint_handles_malformed_json(talisman_client, caplog):
    with caplog.at_level(logging.WARNING):
        resp = talisman_client.post(
            '/csp-report',
            data=b'not-a-json{{{',
            content_type='application/csp-report',
        )
    # silent=True + or {} → endpoint все одно повертає 204
    assert resp.status_code == 204
    # Має бути warning з порожнім payload {}
    assert any('CSP violation' in rec.message for rec in caplog.records)


def test_csp_report_endpoint_rate_limited(talisman_app, talisman_client):
    """101+ POST → 429 (rate-limit 100/hour)."""
    # Reset limiter storage щоб не залежати від попередніх тестів
    from app.extensions import limiter
    limiter.reset()

    statuses = []
    for _ in range(105):
        resp = talisman_client.post(
            '/csp-report',
            json={"csp-report": {"blocked-uri": "x"}},
            content_type='application/csp-report',
        )
        statuses.append(resp.status_code)

    ok = sum(1 for s in statuses if s == 204)
    too_many = sum(1 for s in statuses if s == 429)
    assert ok == 100, f"Expected 100 successful, got {ok}; statuses={statuses}"
    assert too_many == 5, f"Expected 5 rate-limited, got {too_many}"
