# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for PAM landcover → biotope auto-assignment (app/pam/biotope_autoassign.py).

Covers the parts that need neither a live pam_db nor Google Earth Engine:
the pure top-N selection logic, GEE availability gating, the admin route
refusing to run when GEE is unavailable / for non-admins, and constant sanity.
"""
import pytest

from app.pam import biotope_autoassign as ba


# ── select_biotopes_from_histogram ───────────────────────────────────────────

def test_select_top_n_orders_by_pixel_count():
    hist = {10: 500.0, 30: 200.0, 80: 50.0}
    mapping = {10: 1, 30: 2, 80: 3}
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=3) == [1, 2, 3]


def test_select_respects_top_n_cut():
    hist = {10: 500.0, 30: 200.0, 80: 50.0}
    mapping = {10: 1, 30: 2, 80: 3}
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=2) == [1, 2]


def test_select_skips_unmapped_noise_classes():
    hist = {10: 500.0, 50: 300.0, 30: 100.0, 80: 20.0}
    mapping = {10: 1, 30: 2, 80: 3}  # 50 (built-up) unmapped
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=3) == [1, 2, 3]


def test_select_dedupes_biotopes():
    hist = {10: 500.0, 20: 400.0, 30: 100.0}
    mapping = {10: 1, 20: 1, 30: 2}  # two classes → same biotope
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=3) == [1, 2]


def test_select_empty_histogram_returns_empty():
    assert ba.select_biotopes_from_histogram({}, {10: 1}, top_n=3) == []


def test_select_empty_mapping_returns_empty():
    assert ba.select_biotopes_from_histogram({10: 500.0}, {}, top_n=3) == []


# ── GEE availability gating ───────────────────────────────────────────────────

def test_gee_unavailable_without_key(app, monkeypatch):
    monkeypatch.delenv('GEE_SERVICE_ACCOUNT_KEY', raising=False)
    with app.app_context():
        app.config['GEE_SERVICE_ACCOUNT_KEY'] = None
        assert ba.gee_landcover_available() is False


def test_gee_available_with_existing_key(app, tmp_path, monkeypatch):
    key = tmp_path / 'fake_gee_key.json'
    key.write_text('{}', encoding='utf-8')
    monkeypatch.delenv('GEE_SERVICE_ACCOUNT_KEY', raising=False)
    with app.app_context():
        app.config['GEE_SERVICE_ACCOUNT_KEY'] = str(key)
        assert ba.gee_landcover_available() is True


def test_gee_unavailable_when_key_path_missing(app, monkeypatch):
    monkeypatch.delenv('GEE_SERVICE_ACCOUNT_KEY', raising=False)
    with app.app_context():
        app.config['GEE_SERVICE_ACCOUNT_KEY'] = '/nonexistent/path/to/key.json'
        assert ba.gee_landcover_available() is False


# ── Admin route gating ────────────────────────────────────────────────────────

def test_auto_assign_route_returns_503_when_gee_unavailable(auth_client, monkeypatch):
    monkeypatch.setattr(ba, 'gee_landcover_available', lambda: False)
    c = auth_client(role='admin')
    resp = c.post(
        '/uk/pam/admin/biotopes/auto-assign',
        json={'radius_m': 100, 'top_n': 3},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 503
    assert resp.get_json()['success'] is False


def test_auto_assign_route_requires_admin(auth_client):
    c = auth_client(role='analyst')
    resp = c.post(
        '/uk/pam/admin/biotopes/auto-assign',
        json={'radius_m': 100, 'top_n': 3},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code in (302, 401, 403)


# ── Constant / seed consistency ───────────────────────────────────────────────

def test_seed_classes_are_known_worldcover_classes():
    for wc_class in ba.DEFAULT_SEED_BY_NAME_UA:
        assert wc_class in ba.WORLDCOVER_CLASSES


def test_only_forest_is_created_rest_map_to_existing():
    # PAM's biotope set is rich; only the general "Ліс" is added, everything
    # else maps to a pre-existing PAM biotope.
    assert [ua for ua, _ in ba.DEFAULT_LANDCOVER_BIOTOPES] == ['Ліс']
    assert ba.DEFAULT_SEED_BY_NAME_UA.get(10) == 'Ліс'


def test_defaults_are_sane():
    assert ba.DEFAULT_RADIUS_M == 100
    assert ba.DEFAULT_TOP_N == 3
