"""
SEC-003: Auth coverage for SDM blueprint routes.

Verifies that all 6 SDM routes are protected:
  - anonymous -> 302 redirect to login
  - authenticated analyst -> 403 on /enqueue (admin/manager only)
  - authenticated admin -> does not get 302/403/401 on /
"""
import pytest
from unittest.mock import patch, MagicMock


# Patch the SDM adapters so we don't try to connect to the DB
_ADAPTER_PATCHES = [
    patch('app.sdm.routes._species_list', return_value=[]),
    patch('app.sdm.routes._recent_jobs', return_value=[]),
    patch('app.sdm.routes._current_runs', return_value=[]),
]


@pytest.fixture(autouse=True)
def _patch_sdm_adapters():
    """Replace the SDM DB helpers with stubs for all tests in this module."""
    started = [p.start() for p in _ADAPTER_PATCHES]
    yield
    for p in _ADAPTER_PATCHES:
        p.stop()


# ── Anonymous access ─────────────────────────────────────────────────────────

def test_anonymous_cannot_view_dashboard(client):
    resp = client.get('/uk/sdm/')
    assert resp.status_code == 302, (
        f"Анонімний GET /uk/sdm/ → очікувано 302, отримано {resp.status_code}"
    )
    assert '/login' in resp.headers.get('Location', ''), (
        "Redirect має вести на /login"
    )


def test_anonymous_cannot_enqueue(client):
    resp = client.post('/uk/sdm/enqueue', data={'species_code': 'Vulpes_vulpes'})
    assert resp.status_code == 302, (
        f"Анонімний POST /uk/sdm/enqueue → очікувано 302, отримано {resp.status_code}"
    )


def test_anonymous_cannot_view_api_predictions(client):
    resp = client.get('/uk/sdm/api/predictions?species=Vulpes_vulpes')
    assert resp.status_code == 302, (
        f"Анонімний GET /uk/sdm/api/predictions → очікувано 302, отримано {resp.status_code}"
    )


def test_anonymous_cannot_view_map(client):
    resp = client.get('/uk/sdm/map')
    assert resp.status_code == 302


def test_anonymous_cannot_view_coefficients(client):
    resp = client.get('/uk/sdm/coefficients')
    assert resp.status_code == 302


def test_anonymous_cannot_view_job_status(client):
    resp = client.get('/uk/sdm/job/fake-job-id')
    assert resp.status_code == 302


# ── Role-based access ────────────────────────────────────────────────────────

def test_authenticated_admin_can_view_dashboard(auth_client):
    cl = auth_client(role='admin')
    resp = cl.get('/uk/sdm/')
    assert resp.status_code not in (302, 401, 403), (
        f"Admin GET /uk/sdm/ → очікувано не 302/401/403, отримано {resp.status_code}"
    )


def test_authenticated_analyst_cannot_enqueue(auth_client):
    """analyst has access to the dashboard but NOT to /enqueue (admin/manager only)."""
    cl = auth_client(role='analyst')
    resp = cl.post('/uk/sdm/enqueue', data={'species_code': 'Vulpes_vulpes'})
    assert resp.status_code == 403, (
        f"Analyst POST /uk/sdm/enqueue → очікувано 403, отримано {resp.status_code}"
    )


def test_authenticated_analyst_can_view_dashboard(auth_client):
    cl = auth_client(role='analyst')
    resp = cl.get('/uk/sdm/')
    assert resp.status_code not in (302, 401, 403), (
        f"Analyst GET /uk/sdm/ → очікувано не 302/401/403, отримано {resp.status_code}"
    )


def test_authenticated_manager_can_enqueue(auth_client):
    """Manager passes auth on /enqueue (may 500 due to missing worker, but not 302/401/403)."""
    cl = auth_client(role='manager')
    with patch('adapters.worker.create_job', return_value='job-123', create=True):
        resp = cl.post('/uk/sdm/enqueue', data={
            'species_code': 'Vulpes_vulpes',
            'task_name': 'rebuild_model',
        })
    assert resp.status_code not in (302, 401, 403), (
        f"Manager POST /uk/sdm/enqueue → очікувано не 302/401/403, отримано {resp.status_code}"
    )
