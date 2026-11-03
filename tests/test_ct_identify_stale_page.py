"""
/identify left open for long: a clear "reload the page" instead of a JSON error.

Reported 03.11.2026: after stepping away from /identify for a while, submitting
showed "Помилка збереження: Unexpected token '<', "<!doctype "... is not valid
JSON". Not the database: Flask-WTF's CSRF token expired after its default 60
minutes and Werkzeug answered 400 with an HTML page, which the page tried to
parse as JSON. The same happens when the login ends (redirect to the login page).

Fix: the token lives as long as the session (WTF_CSRF_TIME_LIMIT = None), API
callers get JSON with code 'csrf_expired', and the page recognises any stale
answer and offers to reload.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identify_stale_page.py -v
"""
from pathlib import Path

import pytest

TEMPLATE = (Path(__file__).resolve().parents[1]
            / 'app' / 'camera_traps' / 'templates' / 'identification.html')
SUBMIT_URL = '/uk/camera-traps/api/submit-identification'


@pytest.fixture
def csrf_on(app):
    app.config['WTF_CSRF_ENABLED'] = True
    yield app
    app.config['WTF_CSRF_ENABLED'] = False


def test_csrf_token_has_no_time_limit():
    from config import Config
    assert Config.WTF_CSRF_TIME_LIMIT is None


def test_api_gets_json_when_csrf_token_is_missing_or_expired(csrf_on, client):
    resp = client.post(SUBMIT_URL, json={'observation_id': 1})
    assert resp.status_code == 400
    assert resp.is_json
    assert resp.get_json()['code'] == 'csrf_expired'


def test_forms_keep_the_default_html_page(csrf_on, client):
    resp = client.post('/uk/camera-traps/observation/1/flag', data={'note': ''})
    assert resp.status_code == 400
    assert not resp.is_json


def _html():
    return TEMPLATE.read_text(encoding='utf-8')


def test_page_reads_answers_through_readjson():
    html = _html()
    assert 'function readJson(response)' in html
    assert "fetch(nextObservationUrl + (query ? '?' + query : '')).then(readJson)" in html
    submit = html[html.index("url_for('camera_traps.submit_identification'"):][:600]
    assert '.then(readJson)' in submit


def test_stale_answer_offers_reload():
    html = _html()
    assert "data.code === 'csrf_expired'" in html
    assert "response.url.includes('/login')" in html
    assert 'pageStale:' in html
    assert html.count('error.stale) { showStalePage(); return; }') == 2
    assert 'window.location.reload()' in html
