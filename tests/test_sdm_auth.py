"""Maps hub, atlas and SDM access boundaries."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def stub_sdm_data(monkeypatch):
    from app.sdm import routes

    for name in ("_species_list", "_recent_jobs", "_current_runs"):
        monkeypatch.setattr(routes, name, lambda: [])


def test_public_maps_hub(client):
    response = client.get("/uk/sdm/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<a href="/uk/sdm/">Карти</a>' in body
    assert 'href="/uk/sdm/atlas"' in body
    assert 'href="/uk/sdm/admin"' not in body
    assert 'href="/uk/sdm/models"' not in body


@pytest.mark.parametrize("path", [
    "atlas", "api/atlas/ct", "api/atlas/pam", "admin", "map", "models", "coefficients",
    "api/predictions?species=Vulpes_vulpes", "job/fake-job-id",
])
def test_anonymous_protected_pages(client, path):
    response = client.get(f"/uk/sdm/{path}")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_anonymous_cannot_enqueue(client):
    response = client.post("/uk/sdm/enqueue", data={"species_code": "Vulpes_vulpes"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("role", ["manager", "analyst"])
def test_model_reader_cannot_run_or_inspect_jobs(auth_client, role):
    client = auth_client(role=role)
    response = client.get("/en/sdm/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Living atlas" in body
    assert body.index('href="/en/sdm/atlas"') < body.index('href="/en/sdm/map"')
    assert 'href="/en/sdm/admin"' not in body
    for path in ("map", "models", "coefficients"):
        assert client.get(f"/en/sdm/{path}").status_code == 200
    assert client.get("/en/sdm/admin").status_code == 403
    assert client.get("/en/sdm/job/job-123").status_code == 403
    assert client.post("/en/sdm/enqueue", data={"species_code": "Vulpes_vulpes"}).status_code == 403


def test_admin_pages_and_job_actions(auth_client):
    client = auth_client(role="admin")
    hub = client.get("/uk/sdm/").get_data(as_text=True)
    admin = client.get("/uk/sdm/admin")
    assert 'href="/uk/sdm/admin"' in hub
    assert 'id="enqueue-form"' not in hub
    assert admin.status_code == 200
    assert 'id="enqueue-form"' in admin.get_data(as_text=True)
    with patch("adapters.worker.create_job", return_value="job-123") as create_job:
        response = client.post("/uk/sdm/enqueue", data={
            "species_code": "Vulpes_vulpes", "task_name": "rebuild_model",
        })
        assert response.json == {"job_id": "job-123", "status": "queued"}
        assert create_job.call_args.kwargs["payload"]["species_code"] == "Vulpes_vulpes"
        assert client.post("/uk/sdm/enqueue", data={"task_name": "unknown"}).status_code == 400
        assert create_job.call_count == 1
    with patch("adapters.worker.get_job_status", return_value={"status": "running"}):
        assert client.get("/uk/sdm/job/job-123").json == {"status": "running"}


@pytest.mark.parametrize("source", ["ct", "pam"])
def test_atlas_returns_aggregated_species_without_station_coordinates(auth_client, source):
    from app.sdm import atlas

    client = auth_client(role="analyst")
    assert client.get("/en/sdm/atlas").status_code == 200
    day = date(2026, 10, 1)
    data = SimpleNamespace(
        locations={
            1: {"x": 4310100, "y": 3210100, "name": "Private station"},
            2: {"x": 4310500, "y": 3210500},
            3: {"x": 4320500, "y": 3210500},
        },
        detections={1: {9: {day}}, 2: {9: {day}}, 3: {9: set()}, 4: {9: {day}}},
        species={9: {"name_uk": "Лисиця", "name_en": "Red fox", "scientific": "Vulpes vulpes"}},
    )
    with patch.object(atlas, "_monitoring_data", return_value=data) as read_data:
        assert client.get("/en/sdm/api/atlas/unknown").status_code == 404
        response = client.get(f"/en/sdm/api/atlas/{source}")
        read_data.assert_called_once_with(source)
    assert response.status_code == 200
    assert response.cache_control.no_store
    assert response.json["species"] == [{"id": 9, "name": "Red fox", "scientific": "Vulpes vulpes"}]
    feature, = response.json["features"]
    assert feature["properties"] == {"code": "10kmE4310N3210", "species_ids": [9]}
    assert feature["geometry"]["type"] == "Polygon"
    ring, = feature["geometry"]["coordinates"]
    assert len(ring) == 5 and ring[0] == ring[-1]
    assert "Private station" not in response.get_data(as_text=True)
