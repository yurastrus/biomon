"""
Regression tests for the general admin panel i18n (language switching).

Bug: /<lang>/admin/... stayed Ukrainian even when EN was selected. Root cause —
the admin blueprint's url_value_preprocessor pops `lang_code` out of
request.view_args before Babel's locale selector (select_locale) reads it, so
the selector never saw the URL language for admin endpoints and fell back to the
session/default (uk).

Run:
    venv/Scripts/python -m pytest tests/test_admin_i18n.py -v
"""


def test_admin_home_renders_english(auth_client):
    """/en/admin/ must render English admin strings."""
    cl = auth_client(role='admin')
    resp = cl.get('/en/admin/')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'Admin panel' in body          # _('Адмін-панель')
    assert 'Адмін-панель' not in body


def test_admin_home_renders_ukrainian(auth_client):
    """/uk/admin/ must still render Ukrainian."""
    cl = auth_client(role='admin')
    resp = cl.get('/uk/admin/')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'Адмін-панель' in body
    assert 'Admin panel' not in body


def test_admin_users_page_renders_english(auth_client):
    """A second admin page (user list) also switches to EN — proves the fix is
    global (locale selection), not specific to the home page."""
    cl = auth_client(role='admin')
    resp = cl.get('/en/admin/users')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert '<html lang="en">' in body


def test_admin_english_after_ukrainian_in_same_session(auth_client):
    """Switching uk -> en within one session must flip the admin panel to EN
    (the bug left it stuck on the session language)."""
    cl = auth_client(role='admin')
    cl.get('/uk/admin/')                  # seeds session['language'] = 'uk'
    resp = cl.get('/en/admin/')
    body = resp.data.decode('utf-8')
    assert 'Admin panel' in body
    assert 'Адмін-панель' not in body
