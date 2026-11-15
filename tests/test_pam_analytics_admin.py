import pytest

from app.pam import analytics_job


ADMIN_URL = "/uk/pam/admin"
START_URL = f"{ADMIN_URL}/run-analytics"
STATUS_URL = f"{ADMIN_URL}/analytics/status"


def test_pam_analytics_admin_shows_button(auth_client):
    response = auth_client(role="admin").get(ADMIN_URL)
    assert response.status_code == 200
    assert "Перерахувати аналітику" in response.get_data(as_text=True)
    assert START_URL in response.get_data(as_text=True)


@pytest.mark.parametrize(("started", "code"), [(True, 202), (False, 409)])
def test_pam_analytics_start_reports_duplicate(auth_client, monkeypatch, started, code):
    monkeypatch.setattr(analytics_job, "start", lambda: started)
    response = auth_client(role="admin").post(START_URL)
    assert response.status_code == code
    assert response.get_json() == {"status": "running"}


@pytest.mark.parametrize(("method", "url"), [("POST", START_URL), ("GET", STATUS_URL)])
def test_pam_analytics_endpoints_require_admin(auth_client, method, url):
    response = auth_client(role="analyst").open(url, method=method)
    assert response.status_code in (302, 401, 403)


def test_pam_analytics_status_exposes_failure(auth_client, monkeypatch):
    monkeypatch.setattr(analytics_job, "get_status", lambda: {"status": "failed"})
    response = auth_client(role="admin").get(STATUS_URL)
    assert response.status_code == 200
    assert response.get_json() == {"status": "failed"}
