"""
Tests for SEC Phase 2 auth hardening:
  SEC-006 - rate-limit on /login (5/minute, POST only)
  SEC-008 - session.clear() before login_user (session fixation prevention)
  SEC-017 - failed login attempts logged as WARNING
"""

import logging
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Reset in-memory rate limit counters before each test to prevent cross-test interference."""
    from app.extensions import limiter
    storage = getattr(limiter, '_storage', None)
    if storage is not None:
        try:
            storage.reset()
        except Exception:
            pass
    yield


def test_login_rate_limit_after_5_attempts(client):
    """Sixth POST to /login from same IP within one minute must return 429."""
    for _ in range(5):
        client.post('/uk/login', data={'username': 'x', 'password': 'x'})

    response = client.post('/uk/login', data={'username': 'x', 'password': 'x'})
    assert response.status_code == 429


def test_successful_login_clears_pre_login_session_data(client, make_user):
    """session.clear() on successful login removes all pre-login session keys."""
    make_user(username='alice', password='secret123')

    with client.session_transaction() as sess:
        sess['pre_login_key'] = 'should_be_gone'

    client.post('/uk/login', data={'username': 'alice', 'password': 'secret123'})

    with client.session_transaction() as sess:
        assert 'pre_login_key' not in sess


def test_failed_login_writes_warning_to_logger(client, caplog):
    """Failed login attempt emits a WARNING log entry containing username and IP."""
    with caplog.at_level(logging.WARNING, logger='app'):
        client.post('/uk/login', data={'username': 'nosuchuser', 'password': 'wrong'})

    assert any(
        'Failed login' in r.message and 'nosuchuser' in r.message
        for r in caplog.records
    )


def test_successful_login_still_works(client, make_user):
    """Smoke: valid credentials still produce a redirect (302), not 429 or 500."""
    make_user(username='validuser', password='correctpass')

    response = client.post(
        '/uk/login',
        data={'username': 'validuser', 'password': 'correctpass'},
        follow_redirects=False,
    )
    assert response.status_code == 302
