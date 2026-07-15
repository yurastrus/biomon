# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for landcover → biotope auto-assignment (app/camera_traps/biotope_autoassign.py).

Focus on the parts that do not require a live ct_db or Google Earth Engine:
  * the pure top-N class→biotope selection logic (noise-robustness, dedupe);
  * GEE availability gating (drives whether the admin button shows);
  * the admin route refusing to run when GEE is unavailable;
  * seed/constant consistency.
"""
import os
import pytest

from app.camera_traps import biotope_autoassign as ba


# ── select_biotopes_from_histogram ───────────────────────────────────────────

def test_select_top_n_orders_by_pixel_count():
    # class 10 dominant, then 30, then 80; mapping 10→1, 30→2, 80→3.
    hist = {10: 500.0, 30: 200.0, 80: 50.0}
    mapping = {10: 1, 30: 2, 80: 3}
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=3) == [1, 2, 3]


def test_select_respects_top_n_cut():
    hist = {10: 500.0, 30: 200.0, 80: 50.0}
    mapping = {10: 1, 30: 2, 80: 3}
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=2) == [1, 2]


def test_select_skips_unmapped_noise_classes():
    # 50 (built-up) is the 2nd most abundant but unmapped → skipped; the mapped
    # smaller classes still make the top-N, so top_n counts biotopes not classes.
    hist = {10: 500.0, 50: 300.0, 30: 100.0, 80: 20.0}
    mapping = {10: 1, 30: 2, 80: 3}  # 50 intentionally absent
    assert ba.select_biotopes_from_histogram(hist, mapping, top_n=3) == [1, 2, 3]


def test_select_dedupes_biotopes():
    # Two forest classes map to the same biotope → counted once.
    hist = {10: 500.0, 20: 400.0, 30: 100.0}
    mapping = {10: 1, 20: 1, 30: 2}
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
        # earthengine-api is installed in the venv, so availability hinges on the key.
        assert ba.gee_landcover_available() is True


def test_gee_unavailable_when_key_path_missing(app, monkeypatch):
    monkeypatch.delenv('GEE_SERVICE_ACCOUNT_KEY', raising=False)
    with app.app_context():
        app.config['GEE_SERVICE_ACCOUNT_KEY'] = '/nonexistent/path/to/key.json'
        assert ba.gee_landcover_available() is False


# ── Admin route: refuse to run when GEE is unavailable ────────────────────────

def test_auto_assign_route_returns_503_when_gee_unavailable(auth_client, monkeypatch):
    monkeypatch.setattr(ba, 'gee_landcover_available', lambda: False)
    c = auth_client(role='admin')
    resp = c.post(
        '/uk/camera-traps/admin/biotopes/auto-assign',
        json={'radius_m': 100, 'top_n': 3},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 503
    assert resp.get_json()['success'] is False


def test_auto_assign_route_requires_admin(auth_client):
    c = auth_client(role='analyst')
    resp = c.post(
        '/uk/camera-traps/admin/biotopes/auto-assign',
        json={'radius_m': 100, 'top_n': 3},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    # role_required('admin') redirects non-admins away (not a 2xx JSON success).
    assert resp.status_code in (302, 401, 403)


def test_mapping_save_rejects_unknown_class(auth_client):
    c = auth_client(role='admin')
    resp = c.post(
        '/uk/camera-traps/admin/biotopes/mapping',
        json={'worldcover_class': 999, 'biotope_id': None},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert resp.status_code == 400


# ── Constant / seed consistency ───────────────────────────────────────────────

def test_seed_classes_are_known_worldcover_classes():
    for wc_class in ba.DEFAULT_SEED_BY_NAME_UA:
        assert wc_class in ba.WORLDCOVER_CLASSES


def test_defaults_are_sane():
    assert ba.DEFAULT_RADIUS_M == 100
    assert ba.DEFAULT_TOP_N == 3
