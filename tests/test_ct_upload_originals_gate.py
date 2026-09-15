"""
Second storage gate: stop writing full-size originals while the photo volume is
low, without stopping intake.

The hard gate (MIN_UPLOAD_FREE_MB, 500 MB) refuses to start a new batch at all.
This one fires much earlier (MIN_ORIGINALS_FREE_GB, 20 GB): uploads keep working
but only thumbnails are stored, which is what buys the volume weeks of headroom.

Covered:
  1. originals_allowed() thresholds, including the unmeasurable-disk case
  2. process-single downgrades save_original=true server-side when space is low
  3. the upload-fast page disables the checkbox and says why

Run:
    venv/Scripts/python -m pytest tests/test_ct_upload_originals_gate.py -v
"""
from unittest.mock import patch

from app.camera_traps.routes import MIN_ORIGINALS_FREE_BYTES, originals_allowed

GB = 1024 ** 3
PAGE_URL = '/uk/camera-traps/upload-fast'
PROCESS_URL = '/uk/camera-traps/upload/process-single'


def _free(bytes_free):
    """Patch the storage probe as seen from routes.py (imported at call time)."""
    usage = {} if bytes_free is None else {'free_bytes': bytes_free}
    return patch('app.camera_traps.background_tasks.get_storage_disk_usage',
                 return_value=usage)


# ── 1. threshold logic ──────────────────────────────────────────────────────

def test_originals_allowed_above_threshold(app):
    with app.app_context(), _free(50 * GB):
        allowed, free = originals_allowed()
    assert allowed is True
    assert free == 50 * GB


def test_originals_blocked_below_threshold(app):
    with app.app_context(), _free(5 * GB):
        allowed, free = originals_allowed()
    assert allowed is False
    assert free == 5 * GB


def test_originals_allowed_exactly_at_threshold(app):
    """The threshold is inclusive — exactly 20 GB still allows originals."""
    with app.app_context(), _free(MIN_ORIGINALS_FREE_BYTES):
        allowed, _ = originals_allowed()
    assert allowed is True


def test_unmeasurable_disk_does_not_block(app):
    """No storage path configured / not reachable -> allow, never false-block."""
    with app.app_context(), _free(None):
        allowed, free = originals_allowed()
    assert allowed is True
    assert free is None


# ── 2. server-side enforcement on process-single ────────────────────────────

def _post_photo(client, save_original='true'):
    import io
    return client.post(PROCESS_URL, data={
        'location_id': '1',
        'batch_id': 'batch-test',
        'save_original': save_original,
        'file': (io.BytesIO(b'not-a-real-jpeg'), 'IMG_0001.JPG'),
    }, content_type='multipart/form-data')


def test_process_single_downgrades_save_original_when_low(auth_client):
    """Client asks to keep the original, disk is low -> stored as thumbnail only."""
    cl = auth_client(role='manager')
    with _free(5 * GB), \
         patch('app.camera_traps.utils.process_single_photo', return_value=1) as proc:
        _post_photo(cl, save_original='true')
    assert proc.called
    assert proc.call_args.kwargs['save_original'] is False


def test_process_single_keeps_original_when_space_is_fine(auth_client):
    cl = auth_client(role='manager')
    with _free(50 * GB), \
         patch('app.camera_traps.utils.process_single_photo', return_value=1) as proc:
        _post_photo(cl, save_original='true')
    assert proc.called
    assert proc.call_args.kwargs['save_original'] is True


def test_process_single_leaves_compressed_uploads_alone(auth_client):
    """save_original=false needs no disk probe and stays false."""
    cl = auth_client(role='manager')
    with _free(5 * GB), \
         patch('app.camera_traps.utils.process_single_photo', return_value=1) as proc:
        _post_photo(cl, save_original='false')
    assert proc.called
    assert proc.call_args.kwargs['save_original'] is False


# ── 3. page-side hint ───────────────────────────────────────────────────────

def test_upload_page_disables_checkbox_when_low(auth_client):
    cl = auth_client(role='manager')
    with _free(5 * GB):
        resp = cl.get(PAGE_URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="save-original-checkbox"' in html
    assert 'disabled' in html
    assert 'зберігаються лише зменшені копії' in html


def test_upload_page_checkbox_enabled_when_space_is_fine(auth_client):
    cl = auth_client(role='manager')
    with _free(50 * GB):
        resp = cl.get(PAGE_URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'зберігаються лише зменшені копії' not in html
