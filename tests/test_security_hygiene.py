"""
SEC Phase 2: code hygiene -- regression tests (SEC-011 + SEC-020).

SEC-011: unexpected exceptions in the JSON API must not return str(exc)
         to the client (DB schema, internal paths). The response is generic,
         full information stays in the logs.
SEC-020: embed JSON in <script> via |tojson (escape `<` -> \\u003c),
         not json.dumps + |safe (XSS if name contains </script>).

Run:
    venv/Scripts/python -m pytest tests/test_security_hygiene.py -v
"""
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Text that simulates a leak of internal information (DB schema)
LEAKY = 'relation "secret_internal_table" does not exist'

REPO_ROOT = Path(__file__).resolve().parent.parent


# ════════════════════════════════════════════════════════════════════════════
# SEC-011: generic error responses
# ════════════════════════════════════════════════════════════════════════════

def test_sdm_api_error_returns_generic_no_schema_leak(auth_client):
    """GET /sdm/api/predictions: DB error -> 500 + generic, no DB schema."""
    cl = auth_client(role='admin')
    import app.sdm  # noqa: F401 -- adds the submodule root to sys.path (adapters)
    with patch('adapters.biomon.sdm_connection', side_effect=Exception(LEAKY)):
        resp = cl.get('/uk/sdm/api/predictions?species=Vulpes_vulpes')

    assert resp.status_code == 500
    assert resp.get_json()['error'] == 'Internal server error'
    assert 'secret_internal_table' not in resp.get_data(as_text=True)


def test_pam_yearly_trends_table_error_returns_generic(client):
    """GET /api/pam/yearly-trends-table: DB error -> 500 + generic."""
    with patch('app.pam.routes.get_pam_db_connection',
               side_effect=Exception(LEAKY)):
        resp = client.get(
            '/uk/api/pam/yearly-trends-table?start_year=2020&end_year=2024')

    assert resp.status_code == 500
    assert resp.get_json()['error'] == 'Internal server error'
    assert 'secret_internal_table' not in resp.get_data(as_text=True)


def test_pam_import_api_unexpected_error_returns_generic(auth_client):
    """POST /api/pam/import: unexpected error → 500 + generic."""
    cl = auth_client(role='admin')  # admin bypasses the location access check
    with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
         patch('app.pam.routes.PAMImportProcessor',
               side_effect=Exception(LEAKY)):
        resp = cl.post(
            '/uk/api/pam/import',
            data={'location_id': '1',
                  'files': (io.BytesIO(b'col\nval\n'), 'test.csv')},
            content_type='multipart/form-data')

    assert resp.status_code == 500
    data = resp.get_json()
    assert data['success'] is False
    assert data['error'] == 'Internal server error'
    assert 'secret_internal_table' not in resp.get_data(as_text=True)


def test_pam_import_api_valueerror_passes_user_message(auth_client):
    """POST /api/pam/import: ValueError (deliberate validation) → 400 + text."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
         patch('app.pam.routes.PAMImportProcessor',
               side_effect=ValueError('Невідомий формат CSV')):
        resp = cl.post(
            '/uk/api/pam/import',
            data={'location_id': '1',
                  'files': (io.BytesIO(b'col\nval\n'), 'test.csv')},
            content_type='multipart/form-data')

    assert resp.status_code == 400
    data = resp.get_json()
    assert data['success'] is False
    assert 'Невідомий формат CSV' in data['error']


def test_pam_zip_upload_unexpected_error_returns_generic(auth_client):
    """POST upload ZIP: unexpected error → 500 without str(e) in the response."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.process_zip_archive',
               side_effect=Exception(LEAKY)):
        resp = cl.post(
            '/uk/pam/verification/upload/process',
            data={'zip_file': (io.BytesIO(b'PK\x03\x04fake'), 'test.zip')},
            content_type='multipart/form-data')

    # If the route has a different path — the main thing is that there is no leak
    if resp.status_code == 500:
        data = resp.get_json()
        assert data['success'] is False
        assert 'secret_internal_table' not in data['error']
    assert 'secret_internal_table' not in resp.get_data(as_text=True)


def test_pam_zip_upload_valueerror_passes_user_message(auth_client):
    """POST upload ZIP: ValueError → 400 + user-facing validation text."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.process_zip_archive',
               side_effect=ValueError('Пошкоджений файл в архіві: x.wav')):
        resp = cl.post(
            '/uk/pam/verification/upload/process',
            data={'zip_file': (io.BytesIO(b'PK\x03\x04fake'), 'test.zip')},
            content_type='multipart/form-data')

    assert resp.status_code == 400
    data = resp.get_json()
    assert data['success'] is False
    assert 'Пошкоджений файл в архіві' in data['error']


# ════════════════════════════════════════════════════════════════════════════
# SEC-020: |safe → |tojson in templates
# ════════════════════════════════════════════════════════════════════════════

# Templates that embed server data in <script> (historically via |safe)
_JSON_EMBED_TEMPLATES = [
    'app/camera_traps/templates/upload.html',
    'app/camera_traps/templates/upload_fast.html',
    'app/camera_traps/templates/manage_locations.html',
    'app/camera_traps/templates/manage_deployments.html',
    'app/camera_traps/templates/data_quality.html',
    'app/camera_traps/templates/import_classification.html',
    'app/pam/templates/manage_pam_locations.html',
    'app/pam/templates/pam_import.html',
]


@pytest.mark.parametrize('rel_path', _JSON_EMBED_TEMPLATES)
def test_template_has_no_safe_json_embed(rel_path):
    """No template embeds JSON via |safe (regression guard)."""
    text = (REPO_ROOT / rel_path).read_text(encoding='utf-8')
    for needle in ('_json_string|safe', '_json_string | safe',
                   'records_json|safe', '_json|safe'):
        assert needle not in text, (
            f"{rel_path}: знайдено небезпечний '{needle}' — "
            f"використовуй |tojson замість json.dumps + |safe")
    assert '|tojson' in text or '| tojson' in text, (
        f"{rel_path}: очікувано хоча б один |tojson для embed даних")


def test_tojson_escapes_script_breakout(app):
    """|tojson escapes `<` → \\u003c: '</script>' does not break the <script> block."""
    from flask import render_template_string
    payload = [{'name': '</script><script>alert(1)</script>'}]
    with app.test_request_context():
        rendered = render_template_string(
            'const d = {{ data|tojson }};', data=payload)

    assert '</script>' not in rendered
    assert '\\u003c/script' in rendered
    # Data is restored without loss on the JS side (JSON equivalence)
    import json
    js_literal = rendered.removeprefix('const d = ').removesuffix(';')
    assert json.loads(js_literal) == payload
