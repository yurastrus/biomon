"""Maps hub, atlas and SDM access boundaries."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture(autouse=True)
def stub_sdm_data(monkeypatch):
    from app.sdm import routes

    for name in ("_species_list", "_recent_jobs", "_current_runs"):
        monkeypatch.setattr(routes, name, lambda: [])


def test_public_header_links_to_atlas_only_hub(client):
    response = client.get("/uk/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/uk/sdm/"' in body
    hub = client.get("/uk/sdm/")
    assert hub.status_code == 200
    body = hub.get_data(as_text=True)
    assert 'href="/uk/sdm/atlas"' in body
    assert body.count('<a class="hub-card') == 1
    atlas = client.get("/uk/sdm/atlas")
    assert atlas.status_code == 200
    assert 'href="/uk/sdm/" class="ct-back-link"' in atlas.get_data(as_text=True)


@pytest.mark.parametrize("path", [
    "admin", "map", "models", "coefficients",
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


@pytest.mark.parametrize("role", ["manager", "analyst", "viewer"])
def test_only_admin_can_view_models_or_jobs(auth_client, role):
    client = auth_client(role=role)
    hub = client.get("/en/sdm/")
    assert hub.status_code == 200
    assert hub.get_data(as_text=True).count('<a class="hub-card') == 1
    assert client.get("/en/sdm/atlas").status_code == 200
    for path in ("map", "models", "coefficients", "api/predictions", "admin", "job/job-123"):
        assert client.get(f"/en/sdm/{path}").status_code == 403
    assert client.post("/en/sdm/enqueue", data={"species_code": "Vulpes_vulpes"}).status_code == 403


def test_admin_pages_and_job_actions(auth_client):
    client = auth_client(role="admin")
    hub = client.get("/uk/sdm/").get_data(as_text=True)
    admin = client.get("/uk/sdm/admin")
    assert 'href="/uk/sdm/admin"' in hub
    assert hub.index('href="/uk/sdm/atlas"') < hub.index('href="/uk/sdm/map"')
    for path in ("map", "models", "coefficients"):
        assert f'href="/uk/sdm/{path}"' in hub
        assert client.get(f"/uk/sdm/{path}").status_code == 200
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


def test_guest_atlas_apis_use_public_monitoring_scope(client):
    from app.camera_traps import gap_routes as ct
    from app.camera_traps import database
    from app.pam import utils as pam
    from app.sdm import atlas_gbif

    species = {"id": 9, "scientific_name": "Turdus merula", "name_uk": "Дрізд чорний",
               "name_en": None, "class": "Aves", "order_rank": "", "family": "", "genus": ""}
    ct_engine = MagicMock()
    ct_query = ct_engine.connect.return_value.__enter__.return_value.execute
    ct_query.return_value.mappings.return_value.all.return_value = [species]
    pam_conn = MagicMock()
    pam_conn.execute.return_value.mappings.return_value.all.return_value = [species]
    ct_data = SimpleNamespace(locations={}, detections={})
    ct._gap_cache.clear()
    try:
        with (patch.object(database, "get_ct_engine", return_value=ct_engine),
              patch.object(ct.ga, "fetch_gap_data", return_value=ct_data) as read_ct,
              patch.object(pam, "get_pam_db_connection", return_value=pam_conn),
              patch("app.pam.gap_routes.get_pam_db_connection", return_value=pam_conn),
              patch.object(atlas_gbif, "gbif_cells", return_value={"status": "ok", "cells": {}})):
            catalogue = client.get("/uk/sdm/api/atlas/species")
            assert catalogue.status_code == 200
            assert catalogue.cache_control.no_store
            assert catalogue.json["species"][0]["name"] == "Дрізд чорний"
            assert "s.required_role IS NULL" in str(pam_conn.execute.call_args.args[0])

            pam_conn.execute.return_value.all.return_value = [(49.9, 23.7, 0, 1, 0)]
            grid = client.get("/uk/sdm/api/atlas/grid/9")
            assert grid.status_code == 200
            assert grid.cache_control.no_store
            assert not grid.json["unavailable"]
            read_ct.assert_called_once()
            assert read_ct.call_args.args[1] == "l.visibility_level = 0"
            sql = str(pam_conn.execute.call_args.args[0])
            assert "l.visibility_level = 0" in sql
            assert "s.required_role IS NULL" in sql

            response = client.get("/uk/sdm/api/atlas/gbif/9")
            assert response.status_code == 200 and response.json["status"] == "ok"
            assert response.cache_control.no_store
    finally:
        ct._gap_cache.clear()


@pytest.mark.parametrize(("role", "manages", "expected"), [
    ("viewer", False, set()),
    ("pam_verifier", False, {"verify"}),
    ("manager", False, {"verify"}),
    ("manager", True, {"verify", "cut"}),
    ("admin", False, {"verify", "cut"}),
])
def test_atlas_cell_actions_keep_role_and_workflow_location_scope(auth_client, role, manages, expected):
    from app.sdm import atlas, atlas_pam

    client = auth_client(role=role, manages=manages)
    species = {"id": "pam-103", "pam_id": 103, "scientific": "Glaucidium passerinum"}
    with (patch.object(atlas, "_catalogue", return_value=[species]),
          patch.object(atlas_pam, "action_locations", return_value=([10, 20], [20])) as locations):
        response = client.get(
            "/uk/sdm/api/atlas/pam-actions/pam-103?scale=2500&cell=2500mE4312500N3210000"
            "&from=2021&to=2025&month=4&month=6&pam_verified_only=0&pam_confidence=0.85"
        )
    if not expected:
        assert response.status_code == 403
        locations.assert_not_called()
        return
    assert response.status_code == 200
    assert response.cache_control.no_store
    assert locations.call_args.args[1:] == ("2500mE4312500N3210000", 2500)
    assert set(response.json) == expected
    for action, url in response.json.items():
        parts = urlsplit(url)
        outcome = parse_qs(parts.query)
        assert outcome["recording_from"] == ["2021-01-01"]
        assert outcome["recording_to"] == ["2025-12-31"]
        assert outcome["recording_months"] == ["4,6"]
        assert outcome["species_id"] == ["103"]
        if action == "verify":
            assert parts.path.endswith("/verification/verify")
            assert outcome["location_ids"] == ["10,20"]
            assert outcome["scope_conf"] == ["0.85"]
        else:
            assert parts.path.endswith("/verification/segment-window")
            assert outcome["location_ids"] == ["20"]
            assert outcome["conf"] == ["0.85"]


def test_atlas_action_locations_use_exact_cell_and_managed_institutions(monkeypatch):
    from app.pam import utils
    from app.sdm import atlas_pam

    user = SimpleNamespace(is_authenticated=True, has_role=lambda role: role == "manager",
                           allowed_institution_ids=lambda module: [1, 2],
                           manage_institution_ids=lambda module: [2])
    conn = MagicMock()
    conn.execute.return_value.all.return_value = [(10, 1, 1, False), (20, 1, 2, True), (30, 1, 3, True)]
    monkeypatch.setattr(utils, "get_pam_db_connection", lambda: conn)
    projected = {1: (4312499, 3210000), 2: (4312500, 3210000), 3: (4314999, 3212499)}
    monkeypatch.setattr(atlas_pam, "to_laea", lambda lon, lat: projected[lon])
    assert atlas_pam.action_locations(user, "2500mE4312500N3210000", 2500) == ([20, 30], [20, 30])
    sql, params = conn.execute.call_args.args
    assert "visibility_level = 0" in str(sql)
    assert params["user_inst_ids"] == [1, 2]
    assert params["managed"] == [2]


def test_atlas_merges_monitoring_on_selected_grid_and_filters_dates(auth_client):
    from app.sdm import atlas, atlas_pam

    client = auth_client(role="admin")
    page = client.get("/en/sdm/atlas")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'value="2500"' in body
    assert 'id="atlas-pam-verified" type="checkbox" checked' in body
    assert 'value="0.95" step="0.01" disabled' in body
    assert client.get("/en/sdm/static/atlas.js").status_code == 200
    day = date(2025, 10, 1)
    ct = SimpleNamespace(
        locations={
            1: {"x": 4312600, "y": 3210100, "name": "Private station"},
            2: {"x": 4312900, "y": 3210500},
        },
        detections={1: {9: {day, date(2025, 5, 1), date(2024, 10, 1)}},
                    2: {9: {date(2025, 11, 2)}, 12: {day}}, 4: {9: {day}}},
    )
    pam = {"pam_single": {(4312500, 3210000): 1}}
    with (patch.object(atlas, "_catalogue", return_value=[{"id": 9, "ct_id": 9, "pam_id": 42}]),
          patch.object(atlas, "_camera_trap_data", return_value=ct),
          patch.object(atlas_pam, "pam_cells", return_value=pam) as read_pam):
        base = "/en/sdm/api/atlas/grid/9?scale=2500&from=2025&to=2025&month=10&month=11"
        response = client.get(base + "&pam_confidence=ignored")
        read_pam.assert_called_once_with(42, atlas.AtlasFilters(2500, 2025, 2025, (10, 11)),
                                         verified_only=True, confidence=0.95)
        client.get(base + "&pam_verified_only=0&pam_confidence=0.7")
        assert read_pam.call_args.kwargs == {"verified_only": False, "confidence": 0.7}
    assert response.status_code == 200
    assert response.cache_control.no_store
    assert not response.json["unavailable"]
    feature, = response.json["features"]
    assert feature["properties"] == {"code": "2500mE4312500N3210000", "ct": 2, "pam_single": 1}
    assert feature["geometry"]["type"] == "Polygon"
    ring, = feature["geometry"]["coordinates"]
    assert len(ring) == 5 and ring[0] == ring[-1]
    assert "Private station" not in response.get_data(as_text=True)


@pytest.mark.parametrize("query", [
    "scale=2000", "from=2025&to=2024", "month=0", "month=bad", "pam_verified_only=maybe",
    "pam_verified_only=0&pam_confidence=0.69", "pam_verified_only=0&pam_confidence=1.01",
    "pam_verified_only=0&pam_confidence=nan",
])
def test_atlas_rejects_invalid_grid_filters(auth_client, query):
    client = auth_client(role="admin")
    assert client.get(f"/en/sdm/api/atlas/grid/9?{query}").status_code == 400


def test_atlas_catalogue_combines_both_lists_without_duplicates():
    from app.sdm.atlas import _merge_species

    def row(id_, scientific, **fields):
        return {"id": id_, "scientific_name": scientific, "class": "Aves", "order_rank": "",
                "name_uk": None, "name_en": None, "family": "", "genus": "", **fields}

    ct = [row(9, "Turdus merula", genus="Turdus", name_en="Blackbird"), row(10, "Vulpes vulpes")]
    pam = [row(42, "turdus  merula", family="Turdidae"),
           row(103, "Glaucidium passerinum"), row(104, " Turdus merula ")]
    combined = {species["id"]: species for species in _merge_species(ct, pam)}
    assert combined.keys() == {9, 10, "pam-103"}
    species = combined[9]
    assert (species["ct_id"], species["pam_id"]) == (9, 42)
    assert species["name_en"] == "Blackbird"
    assert (species["family"], species["genus"]) == ("Turdidae", "Turdus")
    assert (combined[10]["ct_id"], combined[10]["pam_id"]) == (10, None)
    assert (combined["pam-103"]["ct_id"], combined["pam-103"]["pam_id"]) == (None, 103)


@pytest.mark.parametrize(("atlas_id", "ct_id", "pam_id", "source"), [
    (9, 9, None, "ct"),
    ("pam-103", None, 103, "pam"),
])
def test_atlas_species_from_one_catalogue_only_loads_that_source(
    auth_client, atlas_id, ct_id, pam_id, source,
):
    from app.sdm import atlas, atlas_gbif, atlas_pam

    client = auth_client(role="admin")
    species = {"id": atlas_id, "ct_id": ct_id, "pam_id": pam_id,
               "scientific": "Glaucidium passerinum", "name_en": None, "name_uk": None}
    source_id = ct_id if ct_id is not None else pam_id
    data = SimpleNamespace(locations={1: {"x": 4312670, "y": 3211000}},
                           detections={1: {source_id: {date(2025, 10, 1)}}})
    with (patch.object(atlas, "_catalogue", return_value=[species]),
          patch.object(atlas, "_camera_trap_data", return_value=data) as read_ct,
          patch.object(atlas_pam, "pam_cells",
                       return_value={"pam_consensus": {(4310000, 3210000): 1}}) as read_pam,
          patch.object(atlas_gbif, "gbif_cells", return_value={"status": "ok", "cells": {}}) as gbif):
        catalogue = client.get("/en/sdm/api/atlas/species")
        assert catalogue.json["species"][0]["id"] == atlas_id
        assert catalogue.json["species"][0]["name"] == "Glaucidium passerinum"
        grid = client.get(f"/en/sdm/api/atlas/grid/{atlas_id}?from=2025&to=2025&month=10")
        assert read_ct.call_count == (source == "ct")
        assert read_pam.call_count == (source == "pam")
        assert client.get(f"/en/sdm/api/atlas/gbif/{atlas_id}").json["status"] == "ok"
        assert gbif.call_args.args[0] == species
        assert client.get("/en/sdm/api/atlas/grid/pam-999").status_code == 404
    assert grid.status_code == 200
    cell, = grid.json["features"]
    assert cell["properties"] == {"code": "10kmE4310N3210", ("ct" if ct_id else "pam_consensus"): 1}


def test_gbif_layer_filters_by_month_precision_and_dataset(monkeypatch):
    from app.sdm import atlas_gbif
    from app.sdm.atlas import AtlasFilters

    calls = []
    record = {
        "key": 1, "speciesKey": 42, "datasetKey": "external", "year": 2025, "month": 10,
        "occurrenceStatus": "DETECTED", "decimalLatitude": 49.9, "decimalLongitude": 23.7,
        "coordinateUncertaintyInMeters": 2400,
    }
    records = [record, record, {**record, "key": 2, "month": 7},
               {**record, "key": 3, "datasetKey": "own"},
               {**record, "key": 4, "coordinateUncertaintyInMeters": 2501},
               {**record, "key": 5, "coordinateUncertaintyInMeters": None},
               {**record, "key": 6, "occurrenceStatus": "NOT_DETECTED"}]

    def get(_session, path, params, _deadline):
        calls.append((path, params))
        if path == "species/match":
            return {"matchType": "EXACT", "speciesKey": 42}
        return {"count": len(records), "results": records, "endOfRecords": True}

    monkeypatch.setattr(atlas_gbif, "_get", get)
    filters = AtlasFilters(2500, 2025, 2025, (10,))
    result = atlas_gbif._load("Turdus merula", "Aves", filters, frozenset({"own"}), -1)
    assert result["status"] == "ok" and sum(result["cells"].values()) == 1
    assert calls[0][1]["class"] == "Aves"
    assert calls[1][1]["country"] == "UA"
    assert calls[1][1]["month"] == (10,)
    assert calls[1][1]["coordinateUncertaintyInMeters"] == "0,2500"


def test_gbif_large_query_is_not_rendered_as_partial_data(monkeypatch):
    from app.sdm import atlas_gbif
    from app.sdm.atlas import AtlasFilters

    def get(_session, path, _params, _deadline):
        if path == "species/match":
            return {"matchType": "EXACT", "speciesKey": 42}
        return {"count": atlas_gbif.MAX_RECORDS + 1}

    monkeypatch.setattr(atlas_gbif, "_get", get)
    result = atlas_gbif._load("Turdus merula", "Aves", AtlasFilters(1000, 2025, 2025, ()),
                             frozenset(), -1)
    assert result["status"] == "too_many"
    assert not result["cells"]
