"""
Batch time shift on /upload-fast: one clock correction for every photo of a batch.

The offset lives on UploadBatch.time_offset_seconds and is added by the server
to each photo's EXIF time. The browser only previews it.

Covered:
  1. parse_time_offset — what the server accepts as an offset
  2. extract_datetime_from_exif(offset=...) — shift before the plausibility guard
  3. /api/create-batch — validation, pass-through, response
  4. create_upload_batch / get_batch_status — stored and reported
  5. process_single_photo end to end (SQLite + real JPEGs): shifted EXIF time,
     unshifted placeholder and video override, rescued camera reset, duplicates
  6. /upload-fast page wiring; legacy /upload left untouched; JS limit in step

Run:
    venv/Scripts/python -m pytest tests/test_ct_upload_time_shift.py -v
"""
import io
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.camera_traps.utils import (
    MAX_TIME_OFFSET_SECONDS,
    extract_datetime_from_exif,
    parse_time_offset,
)

HOUR = 3600
DAY = 24 * HOUR
CREATE_URL = '/uk/camera-traps/api/create-batch'
FAST_PAGE_URL = '/uk/camera-traps/upload-fast'
LEGACY_PAGE_URL = '/uk/camera-traps/upload'


# ── helpers ─────────────────────────────────────────────────────────────────

def _jpeg(date_str=None, subsec=None):
    """A real little JPEG, optionally carrying EXIF DateTimeOriginal."""
    img = Image.new('RGB', (40, 30), 'green')
    buf = io.BytesIO()
    if date_str is None:
        img.save(buf, 'JPEG')
    else:
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9003] = date_str
        if subsec is not None:
            exif.get_ifd(0x8769)[0x9291] = subsec
        img.save(buf, 'JPEG', exif=exif)
    buf.seek(0)
    return buf


def _mock_exif_date(monkeypatch, date_str, subsec=None):
    tags = {'EXIF DateTimeOriginal': date_str}
    if subsec is not None:
        tags['EXIF SubSecTimeOriginal'] = subsec
    monkeypatch.setattr('app.camera_traps.utils.exifread.process_file',
                        lambda *a, **kw: tags)


# ── 1. parse_time_offset ────────────────────────────────────────────────────

@pytest.mark.parametrize('value, expected', [
    (None, 0),
    ('', 0),
    (0, 0),
    (-3600, -3600),
    (43200, 43200),
    ('3600', 3600),
    (' -86400 ', -86400),
    (7200.0, 7200),
    (MAX_TIME_OFFSET_SECONDS, MAX_TIME_OFFSET_SECONDS),
    (-MAX_TIME_OFFSET_SECONDS, -MAX_TIME_OFFSET_SECONDS),
])
def test_parse_time_offset_accepts(value, expected):
    assert parse_time_offset(value) == expected


@pytest.mark.parametrize('value', [
    True, False,                       # bool is an int subclass: refuse explicitly
    1.5,                               # sub-second shifts are not a thing
    'abc', '1h', '3600.5',
    MAX_TIME_OFFSET_SECONDS + 1,
    -MAX_TIME_OFFSET_SECONDS - 1,
    [3600], {'s': 1},
])
def test_parse_time_offset_rejects(value):
    with pytest.raises(ValueError):
        parse_time_offset(value)


def test_max_offset_covers_a_factory_reset_to_2000():
    """The limit must allow the realistic worst case: a camera that fell back to
    2000-01-01 and is being moved to today."""
    assert MAX_TIME_OFFSET_SECONDS > (datetime(2026, 12, 31) - datetime(2000, 1, 1)).total_seconds()
    assert MAX_TIME_OFFSET_SECONDS < 2 ** 31   # fits the INTEGER column


# ── 2. extract_datetime_from_exif with an offset ────────────────────────────

def test_offset_none_keeps_old_behaviour(monkeypatch):
    _mock_exif_date(monkeypatch, '2026:04:17 10:00:05')
    assert extract_datetime_from_exif(io.BytesIO(b'x')) == datetime(2026, 4, 17, 10, 0, 5)


def test_negative_offset_moves_time_back(monkeypatch):
    """Summer time on the camera: -1 hour."""
    _mock_exif_date(monkeypatch, '2026:04:17 10:00:05')
    got = extract_datetime_from_exif(io.BytesIO(b'x'), offset=timedelta(seconds=-HOUR))
    assert got == datetime(2026, 4, 17, 9, 0, 5)


def test_am_pm_mixup_plus_twelve_hours(monkeypatch):
    _mock_exif_date(monkeypatch, '2026:04:17 05:30:00')
    got = extract_datetime_from_exif(io.BytesIO(b'x'), offset=timedelta(hours=12))
    assert got == datetime(2026, 4, 17, 17, 30, 0)


def test_wrong_day_minus_sixteen_days_crosses_month(monkeypatch):
    _mock_exif_date(monkeypatch, '2026:05:10 08:00:00')
    got = extract_datetime_from_exif(io.BytesIO(b'x'), offset=timedelta(days=-16))
    assert got == datetime(2026, 4, 24, 8, 0, 0)


def test_offset_keeps_subseconds(monkeypatch):
    _mock_exif_date(monkeypatch, '2026:04:17 10:00:05', subsec='25')
    got = extract_datetime_from_exif(io.BytesIO(b'x'), offset=timedelta(seconds=-HOUR))
    assert got == datetime(2026, 4, 17, 9, 0, 5, 250000)


def test_offset_rescues_camera_reset_to_2000(app, monkeypatch):
    """Without a shift 2000-01-01 is implausible (-> placeholder). The shift is
    applied BEFORE the guard, so correcting the clock makes the photo usable."""
    _mock_exif_date(monkeypatch, '2000:01:01 12:00:00')
    with app.app_context():
        assert extract_datetime_from_exif(io.BytesIO(b'x')) is None
        fix = datetime(2026, 4, 17, 12, 0, 0) - datetime(2000, 1, 1, 12, 0, 0)
        assert extract_datetime_from_exif(io.BytesIO(b'x'), offset=fix) == datetime(2026, 4, 17, 12, 0, 0)


def test_offset_into_the_future_is_still_refused(app, monkeypatch):
    """The guard runs on the shifted value: a shift cannot smuggle in a future date."""
    _mock_exif_date(monkeypatch, datetime.now().strftime('%Y:%m:%d %H:%M:%S'))
    with app.app_context():
        assert extract_datetime_from_exif(io.BytesIO(b'x'), offset=timedelta(days=3)) is None


def test_offset_below_2010_is_refused(app, monkeypatch):
    _mock_exif_date(monkeypatch, '2010:01:05 00:00:00')
    with app.app_context():
        assert extract_datetime_from_exif(io.BytesIO(b'x'), offset=timedelta(days=-10)) is None


# ── 3. /api/create-batch ────────────────────────────────────────────────────

def _create(client, payload):
    with patch('app.camera_traps.utils.create_upload_batch', return_value='b-1') as cub, \
         patch('app.camera_traps.background_tasks.get_storage_disk_usage',
               return_value={'free_bytes': 100 * 1024 ** 3}), \
         patch('app.camera_traps.ai_runner.pause_ai_classification'):
        resp = client.post(CREATE_URL, json=payload)
    return resp, cub


def test_create_batch_without_offset_defaults_to_zero(auth_client):
    """Old clients (and legacy /upload) send no offset: unchanged behaviour."""
    resp, cub = _create(auth_client(role='manager'), {'location_id': 5, 'total_files': 3})
    assert resp.status_code == 201
    assert cub.call_args.kwargs['time_offset_seconds'] == 0
    assert resp.get_json()['time_offset_seconds'] == 0


def test_create_batch_passes_offset_through(auth_client):
    resp, cub = _create(auth_client(role='manager'),
                        {'location_id': 5, 'total_files': 3, 'time_offset_seconds': -3600})
    assert resp.status_code == 201
    assert cub.call_args.kwargs['time_offset_seconds'] == -3600
    assert resp.get_json()['time_offset_seconds'] == -3600


@pytest.mark.parametrize('bad', ['abc', 1.5, True, MAX_TIME_OFFSET_SECONDS + 1])
def test_create_batch_rejects_bad_offset_before_creating(auth_client, bad):
    resp, cub = _create(auth_client(role='manager'),
                        {'location_id': 5, 'total_files': 3, 'time_offset_seconds': bad})
    assert resp.status_code == 400
    assert not cub.called


def test_create_batch_offset_still_needs_manager(auth_client):
    resp, cub = _create(auth_client(role='viewer'),
                        {'location_id': 5, 'time_offset_seconds': 3600})
    assert resp.status_code in (302, 403)
    assert not cub.called


# ── 4./5. storage + process_single_photo end to end (SQLite) ────────────────

@pytest.fixture
def ct_env(app, ct_session, make_ct_location, tmp_path, monkeypatch):
    """Route the uploader at the in-memory CT DB and a temp upload folder.

    The config is built from config.Config, not copied from app.config: the
    `app` fixture is shared, and other tests overwrite CAMERA_TRAP_CONFIG with
    partial dicts (no THUMBNAIL_SIZE), which made these tests order-dependent."""
    from config import Config
    cfg = dict(Config.CAMERA_TRAP_CONFIG)
    cfg['UPLOAD_PATH'] = str(tmp_path)
    monkeypatch.setitem(app.config, 'CAMERA_TRAP_CONFIG', cfg)
    monkeypatch.setattr('app.camera_traps.utils.get_ct_session', lambda: ct_session)
    monkeypatch.setattr('app.camera_traps.utils.close_ct_session', lambda: None)
    loc = make_ct_location()
    with app.test_request_context():
        yield {'session': ct_session, 'location': loc}


def _batch(env, offset=0):
    from app.camera_traps.utils import create_upload_batch
    return create_upload_batch(env['location'].id, 1, 1, time_offset_seconds=offset)


def _process(env, batch_id, buf, name='IMG_0001.JPG', **kw):
    from app.camera_traps.utils import process_single_photo
    from app.camera_traps.models import Photo
    fs = FileStorage(stream=buf, filename=name, content_type='image/jpeg')
    pid = process_single_photo(fs, env['location'].id, 1, batch_id,
                               save_original=False, **kw)
    return env['session'].get(Photo, pid)


def test_create_upload_batch_stores_offset(ct_env):
    from app.camera_traps.models import UploadBatch
    bid = _batch(ct_env, -3600)
    assert ct_env['session'].get(UploadBatch, bid).time_offset_seconds == -3600


def test_create_upload_batch_default_offset_is_zero(ct_env):
    from app.camera_traps.utils import create_upload_batch
    from app.camera_traps.models import UploadBatch
    bid = create_upload_batch(ct_env['location'].id, 1, 1)
    assert ct_env['session'].get(UploadBatch, bid).time_offset_seconds == 0


def test_batch_status_reports_offset(ct_env):
    from app.camera_traps.utils import get_batch_status
    bid = _batch(ct_env, 12 * HOUR)
    assert get_batch_status(bid)['time_offset_seconds'] == 12 * HOUR


def test_process_single_photo_applies_batch_offset(ct_env):
    bid = _batch(ct_env, -HOUR)
    photo = _process(ct_env, bid, _jpeg('2026:04:17 10:00:05'))
    assert photo.captured_at == datetime(2026, 4, 17, 9, 0, 5)
    # The system filename is built from the stored (shifted) time.
    assert '20260417_090005' in photo.system_filename


def test_process_single_photo_without_offset_is_unchanged(ct_env):
    bid = _batch(ct_env, 0)
    photo = _process(ct_env, bid, _jpeg('2026:04:17 10:00:05'))
    assert photo.captured_at == datetime(2026, 4, 17, 10, 0, 5)


def test_original_time_is_recoverable(ct_env):
    """captured_at - offset gives back exactly what the camera wrote."""
    from app.camera_traps.models import UploadBatch
    off = -16 * DAY + 3 * HOUR
    bid = _batch(ct_env, off)
    photo = _process(ct_env, bid, _jpeg('2026:04:17 10:00:05', subsec='5'))
    batch = ct_env['session'].get(UploadBatch, bid)
    assert photo.captured_at - timedelta(seconds=batch.time_offset_seconds) == \
        datetime(2026, 4, 17, 10, 0, 5, 500000)


def test_placeholder_for_missing_exif_is_not_shifted(ct_env):
    """No EXIF -> the 1900 placeholder. Shifting it would only blur the marker."""
    bid = _batch(ct_env, 5 * DAY)
    photo = _process(ct_env, bid, _jpeg(None))
    assert photo.captured_at.year == 1900
    assert photo.captured_at < datetime(1900, 1, 2)


def test_video_override_is_not_shifted(ct_env):
    """A video frame's time is read from its burned-in stamp: already final."""
    bid = _batch(ct_env, -HOUR)
    when = datetime(2026, 4, 17, 10, 0, 5)
    photo = _process(ct_env, bid, _jpeg(None), captured_at_override=when)
    assert photo.captured_at == when


def test_camera_reset_is_rescued_end_to_end(ct_env):
    fix = int((datetime(2026, 4, 17, 12, 0, 0) - datetime(2000, 1, 1, 12, 0, 0)).total_seconds())
    bid = _batch(ct_env, fix)
    photo = _process(ct_env, bid, _jpeg('2000:01:01 12:00:00'))
    assert photo.captured_at == datetime(2026, 4, 17, 12, 0, 0)


def test_duplicate_check_uses_shifted_time(ct_env):
    """Same file twice into a shifted batch -> the second is a duplicate."""
    bid = _batch(ct_env, -HOUR)
    _process(ct_env, bid, _jpeg('2026:04:17 10:00:05'))
    with pytest.raises(ValueError, match='Duplicate'):
        _process(ct_env, bid, _jpeg('2026:04:17 10:00:05'))


def test_same_file_with_other_offset_is_a_different_photo(ct_env):
    """Documents a known limit: the duplicate key is the stored time, so the same
    file uploaded again with another shift is NOT recognised as a duplicate."""
    _process(ct_env, _batch(ct_env, 0), _jpeg('2026:04:17 10:00:05'))
    photo = _process(ct_env, _batch(ct_env, -HOUR), _jpeg('2026:04:17 10:00:05'))
    assert photo.captured_at == datetime(2026, 4, 17, 9, 0, 5)


def test_unshifted_duplicate_detection_still_works(ct_env):
    """Regression: plain batches (offset 0) keep catching duplicates."""
    _process(ct_env, _batch(ct_env, 0), _jpeg('2026:04:17 10:00:05'))
    with pytest.raises(ValueError, match='Duplicate'):
        _process(ct_env, _batch(ct_env, 0), _jpeg('2026:04:17 10:00:05'))


# ── 6. page wiring ──────────────────────────────────────────────────────────

def _page(client, url):
    with patch('app.camera_traps.background_tasks.get_storage_disk_usage',
               return_value={'free_bytes': 100 * 1024 ** 3}):
        resp = client.get(url)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_fast_page_has_time_shift_block(auth_client):
    html = _page(auth_client(role='manager'), FAST_PAGE_URL)
    for marker in ('id="time-shift-block"', 'id="ts-days"', 'id="ts-hours"',
                   'id="ts-minutes"', 'id="ts-seconds"', 'id="ts-sign"',
                   'id="ts-current"', 'id="ts-desired"', 'id="ts-calc-btn"',
                   'id="ts-preview-body"', 'js/time_shift.js', 'exifr@',
                   'time_offset_seconds'):
        assert marker in html, marker


def test_fast_page_time_shift_is_collapsed_by_default(auth_client):
    """Optional feature: closed <details>, so nobody shifts time by accident."""
    html = _page(auth_client(role='manager'), FAST_PAGE_URL)
    assert '<details id="time-shift-block">' in html


def test_legacy_upload_page_has_no_time_shift(auth_client):
    html = _page(auth_client(role='manager'), LEGACY_PAGE_URL)
    assert 'time-shift-block' not in html
    assert 'time_shift.js' not in html


def test_js_limit_matches_server_limit(app):
    """time_shift.js and utils.py must agree on the maximum offset."""
    import os
    import re
    path = os.path.join(app.root_path, 'camera_traps', 'static', 'js', 'time_shift.js')
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    m = re.search(r'const MAX_OFFSET_SECONDS = ([\d\s*]+);', src)
    assert m, 'MAX_OFFSET_SECONDS not found in time_shift.js'
    js_value = 1
    for factor in m.group(1).split('*'):
        js_value *= int(factor)
    assert js_value == MAX_TIME_OFFSET_SECONDS


def test_time_shift_js_is_served(auth_client):
    resp = auth_client(role='manager').get('/uk/camera-traps/ct-static/js/time_shift.js')
    assert resp.status_code == 200
    assert b'CtTimeShift' in resp.data


def test_fast_page_time_shift_is_translated(auth_client):
    html = _page(auth_client(role='manager'), '/en/camera-traps/upload-fast')
    for text in ('Time shift', 'Calculate the shift from an example', 'Correct time',
                 'How the time will change'):
        assert text in html, text
    assert 'Зсув часу' not in html
