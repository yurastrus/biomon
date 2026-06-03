"""
Idea 3: storage/batch health-метрики у CT адмін-панелі.

admin_panel() підтягує get_cleanup_statistics() + get_batch_statistics()
і передає їх у admin.html новою секцією «Сховище та батчі».
Перевіряємо: рендер чисел, graceful-degradation при помилці, доступ.

Запуск:
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
    assert 'completed' in html    # batches_by_status ключ


def test_storage_section_handles_missing_oldest_batch(auth_client):
    cl = auth_client(role='admin')
    batch = {**FAKE_BATCH, 'oldest_pending_batch': None}
    p1, p2 = _patch_stats(batch=batch)
    with p1, p2:
        resp = cl.get(URL)
    assert resp.status_code == 200
    # Немає незавершених батчів → прочерк, не падіння шаблону
    assert 'Вік найстарішого незавершеного батчу' in resp.get_data(as_text=True)


def test_admin_panel_survives_stats_exception(auth_client):
    """get_cleanup_statistics кидає виняток → сторінка все одно 200,
    секція показує дефолтні нулі (як ai_stats fallback)."""
    cl = auth_client(role='admin')
    p1, p2 = _patch_stats(cleanup_exc=Exception('boom'), batch={})
    with p1, p2:
        resp = cl.get(URL)
    assert resp.status_code == 200
    assert 'Сховище та батчі' in resp.get_data(as_text=True)


def test_storage_section_requires_admin(auth_client):
    """Не-admin (manager) не має доступу до CT адмін-панелі (CT → 302)."""
    cl = auth_client(role='manager')
    resp = cl.get(URL)
    assert resp.status_code in (302, 403)
