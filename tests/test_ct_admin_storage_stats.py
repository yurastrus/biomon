"""
Idea 3: storage/batch health metrics in the CT admin panel.

admin_panel() pulls in get_cleanup_statistics() + get_batch_statistics()
and passes them to admin.html in a new "Storage and batches" section.
We verify: number rendering, graceful degradation on error, access.

Run:
    venv/Scripts/python -m pytest tests/test_ct_admin_storage_stats.py -v
"""
from unittest.mock import patch

URL = '/uk/camera-traps/admin'

FAKE_CLEANUP = {
    'observations_count': 7,
    'photos_count': 140,
    'estimated_size_mb': 280,
}
FAKE_BATCH = {
    'batches_by_status': {'completed': 5, 'failed': 2},
    'orphaned_photos': 13,
    'oldest_pending_batch': {
        'id': 'abc', 'status': 'uploading',
        'created_at': '2026-06-01T00:00:00', 'age_hours': 5.5,
    },
}


def _patch_stats(cleanup=FAKE_CLEANUP, batch=FAKE_BATCH, cleanup_exc=None):
    cl_kw = {'side_effect': cleanup_exc} if cleanup_exc else {'return_value': cleanup}
    return (
        patch('app.camera_traps.background_tasks.get_cleanup_statistics', **cl_kw),
        patch('app.camera_traps.background_tasks.get_batch_statistics',
              return_value=batch),
    )


def test_storage_section_renders_numbers(auth_client):
    cl = auth_client(role='admin')
    p1, p2 = _patch_stats()
    with p1, p2:
        resp = cl.get(URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Сховище та батчі' in html
    assert 'Серій готових до архівації' in html
    assert 'Фото без серій (сироти)' in html
    assert '280' in html          # estimated_size_mb
    assert '13' in html           # orphaned_photos
    assert '5.5' in html          # oldest batch age
    assert 'completed' in html    # batches_by_status key


def test_storage_section_handles_missing_oldest_batch(auth_client):
    cl = auth_client(role='admin')
    batch = {**FAKE_BATCH, 'oldest_pending_batch': None}
    p1, p2 = _patch_stats(batch=batch)
    with p1, p2:
        resp = cl.get(URL)
    assert resp.status_code == 200
    # No pending batches -> dash, not a template crash
    assert 'Вік найстарішого незавершеного батчу' in resp.get_data(as_text=True)


def test_admin_panel_survives_stats_exception(auth_client):
    """get_cleanup_statistics raises an exception -> page still returns 200,
    section shows default zeros (like the ai_stats fallback)."""
    cl = auth_client(role='admin')
    p1, p2 = _patch_stats(cleanup_exc=Exception('boom'), batch={})
    with p1, p2:
        resp = cl.get(URL)
    assert resp.status_code == 200
    assert 'Сховище та батчі' in resp.get_data(as_text=True)


def test_storage_section_requires_admin(auth_client):
    """Non-admin (manager) has no access to the CT admin panel (CT -> 302)."""
    cl = auth_client(role='manager')
    resp = cl.get(URL)
    assert resp.status_code in (302, 403)


# ── get_storage_disk_usage() — page-side `df -h` for the photo storage ──────
from unittest.mock import MagicMock  # noqa: E402
from app.camera_traps.background_tasks import get_storage_disk_usage  # noqa: E402


def _du(total, used, free):
    m = MagicMock()
    m.total, m.used, m.free = total, used, free
    return m


def test_disk_usage_reports_free_bytes(app, tmp_path):
    """Reads UPLOAD_PATH from CAMERA_TRAP_CONFIG and returns byte counts."""
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'UPLOAD_PATH': str(tmp_path)}
        with patch('app.camera_traps.background_tasks.shutil.disk_usage',
                   return_value=_du(100, 40, 60)) as du:
            result = get_storage_disk_usage()
    assert result['free_bytes'] == 60
    assert result['total_bytes'] == 100
    assert result['path'] == str(tmp_path)
    du.assert_called_once()


def test_disk_usage_walks_up_to_existing_parent(app, tmp_path):
    """A not-yet-created uploads subfolder still resolves to its filesystem."""
    missing = tmp_path / 'not' / 'created' / 'yet'
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'UPLOAD_PATH': str(missing)}
        with patch('app.camera_traps.background_tasks.shutil.disk_usage',
                   return_value=_du(100, 40, 60)) as du:
            result = get_storage_disk_usage()
    assert result['free_bytes'] == 60
    # probed an existing ancestor, not the missing leaf
    probed = du.call_args[0][0]
    import os
    assert os.path.exists(probed)


def test_disk_usage_empty_when_no_upload_path(app):
    """No UPLOAD_PATH configured -> {} (template renders a dash)."""
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {}
        assert get_storage_disk_usage() == {}


def test_disk_usage_empty_on_oserror(app, tmp_path):
    """disk_usage raising OSError degrades to {} instead of a 500."""
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'UPLOAD_PATH': str(tmp_path)}
        with patch('app.camera_traps.background_tasks.shutil.disk_usage',
                   side_effect=OSError('gone')):
            assert get_storage_disk_usage() == {}


def test_admin_card_shows_free_space_in_gb(auth_client):
    """The new first card renders human-readable free space (GB above 1 GiB)."""
    cl = auth_client(role='admin')
    p1, p2 = _patch_stats()
    with p1, p2, patch(
            'app.camera_traps.background_tasks.get_storage_disk_usage',
            return_value={'path': '/data', 'total_bytes': 40 * 1024**3,
                          'used_bytes': 15 * 1024**3, 'free_bytes': 25 * 1024**3}):
        resp = cl.get(URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Доступно місця у сховищі' in html
    assert '25.0' in html   # 25 GiB free rendered with one decimal


def test_admin_card_shows_free_space_in_mb_below_1gb(auth_client):
    """Under 1 GiB free -> shown in MB per the requested threshold."""
    cl = auth_client(role='admin')
    p1, p2 = _patch_stats()
    with p1, p2, patch(
            'app.camera_traps.background_tasks.get_storage_disk_usage',
            return_value={'path': '/data', 'total_bytes': 40 * 1024**3,
                          'used_bytes': 39 * 1024**3,
                          'free_bytes': 500 * 1024**2}):
        resp = cl.get(URL)
    html = resp.get_data(as_text=True)
    assert '500' in html    # 500 MiB free
