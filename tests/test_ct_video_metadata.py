"""Capture time from a video container header, and the operator-confirmed path.

The headers are built byte by byte here, laid out as the cameras in the archives
write them (checked with exiftool on SYPR and NVTIM MOV, UOVision and
Fujifilm-style AVI), so the suite needs no sample videos.
"""

import struct
from datetime import datetime, timedelta
from io import BytesIO

import pytest

from app.camera_traps import video_metadata as vm
from app.camera_traps import video_upload as vu

QT_EPOCH = datetime(1904, 1, 1)


def _box(kind, payload):
    return struct.pack('>I4s', 8 + len(payload), kind) + payload


def _mvhd(created, duration_s=10, scale=1000, version=0):
    seconds = int((created - QT_EPOCH).total_seconds()) if created else 0
    if version == 1:
        body = struct.pack('>QQIQ', seconds, seconds, scale, int(duration_s * scale))
    else:
        body = struct.pack('>IIII', seconds, seconds, scale, int(duration_s * scale))
    return _box(b'mvhd', bytes([version, 0, 0, 0]) + body + b'\x00' * 80)


def _moov(created, **kwargs):
    # mvhd first, a track after it, as cameras write it.
    return _box(b'moov', _mvhd(created, **kwargs) + _box(b'trak', b'\x00' * 32))


def _avi(frames=100, micros=50000, idit=None):
    avih = struct.pack('<4sI', b'avih', 56) + struct.pack(
        '<IIIII', micros, 0, 0, 0, frames) + b'\x00' * 36
    hdrl = b'hdrl' + avih
    if idit is not None:
        text = idit.encode('ascii') + b'\x00'
        hdrl += struct.pack('<4sI', b'IDIT', len(text)) + text
    body = b'AVI ' + struct.pack('<4sI', b'LIST', len(hdrl)) + hdrl
    return b'RIFF' + struct.pack('<I', len(body)) + body


# ─── QuickTime ───────────────────────────────────────────────────────────────

def test_creation_time_is_read_from_the_moov_box():
    result = vm.container_times(_moov(datetime(2026, 6, 13, 6, 53, 41)))
    assert result['created'] == '2026-06-13T06:53:41'
    assert result['duration'] == 10.0
    assert result['plausible'] is True
    assert result['container'] == 'quicktime'


def test_the_value_is_taken_as_the_cameras_local_clock():
    """QuickTime says UTC; camera traps write local time. Nothing is converted."""
    result = vm.container_times(_moov(datetime(2025, 4, 3, 12, 7, 43)))
    assert result['created'] == '2025-04-03T12:07:43'


def test_version_1_mvhd_with_64_bit_fields():
    result = vm.container_times(_moov(datetime(2025, 3, 22, 15, 46, 7),
                                      version=1, duration_s=20))
    assert result['created'] == '2025-03-22T15:46:07'
    assert result['duration'] == 20.0


def test_a_zero_creation_time_means_unknown_not_1904():
    result = vm.container_times(_moov(None))
    assert result['created'] is None
    assert result['plausible'] is False


def test_a_factory_default_year_is_reported_but_flagged():
    """The operator should see that the clock was never set."""
    result = vm.container_times(_moov(datetime(2000, 1, 1, 0, 0, 5)))
    assert result['created'] == '2000-01-01T00:00:05'
    assert result['plausible'] is False


def test_a_future_date_is_flagged():
    now = datetime(2026, 11, 3, 12, 0, 0)
    result = vm.container_times(_moov(datetime(2027, 1, 1)), now=now)
    assert result['plausible'] is False


def test_a_moov_found_after_other_top_level_boxes():
    data = (_box(b'ftyp', b'qt  ' + b'\x00' * 12) + _box(b'mdat', b'\x00' * 500)
            + _moov(datetime(2026, 6, 13, 6, 54, 11)))
    assert vm.container_times(data)['created'] == '2026-06-13T06:54:11'


def test_garbage_yields_nothing_rather_than_an_error():
    result = vm.container_times(b'\x00\x01garbage' * 10)
    assert result['created'] is None
    assert result['plausible'] is False


def test_a_truncated_moov_yields_nothing():
    assert vm.container_times(_moov(datetime(2026, 6, 13))[:20])['created'] is None


# ─── AVI ─────────────────────────────────────────────────────────────────────

def test_an_avi_without_idit_has_a_duration_and_no_date():
    """UOVision and Fujifilm-style AVIs in the archives look exactly like this."""
    result = vm.container_times(_avi(frames=100, micros=50000))
    assert result['container'] == 'avi'
    assert result['created'] is None
    assert result['duration'] == 5.0


@pytest.mark.parametrize('text', ['Sat Mar 22 15:46:07 2025', '2025:03:22 15:46:07'])
def test_an_avi_idit_chunk_is_read(text):
    result = vm.container_times(_avi(idit=text))
    assert result['created'] == '2025-03-22T15:46:07'
    assert result['plausible'] is True


# ─── Operator-confirmed start ────────────────────────────────────────────────

def test_operator_start_accepts_a_plain_local_time():
    assert vu.operator_start('2025-08-13T13:09:41') == datetime(2025, 8, 13, 13, 9, 41)


def test_operator_start_drops_a_timezone_rather_than_converting():
    assert vu.operator_start('2025-08-13T13:09:41+03:00') == \
        datetime(2025, 8, 13, 13, 9, 41)


@pytest.mark.parametrize('raw', ['', 'yesterday', '2025-13-01T00:00:00'])
def test_operator_start_refuses_what_is_not_a_date(raw):
    with pytest.raises(vu.VideoUploadError):
        vu.operator_start(raw)


@pytest.mark.parametrize('raw', ['2000-01-01T00:00:00', '2099-01-01T00:00:00'])
def test_operator_start_refuses_an_impossible_capture_time(raw):
    with pytest.raises(vu.VideoUploadError) as info:
        vu.operator_start(raw)
    assert info.value.code == vu.CODE_IMPLAUSIBLE_START


# ─── Endpoints ───────────────────────────────────────────────────────────────

def test_metadata_endpoint_parses_an_uploaded_header(auth_client):
    client = auth_client(role='admin')
    data = {'header': (BytesIO(_moov(datetime(2026, 6, 13, 6, 53, 41))), 'header.bin')}
    response = client.post('/uk/camera-traps/api/video/metadata', data=data,
                           content_type='multipart/form-data')
    assert response.status_code == 200
    assert response.get_json()['created'] == '2026-06-13T06:53:41'


def test_sign_clip_issues_a_token_that_dates_the_frames(auth_client, app):
    client = auth_client(role='admin')
    response = client.post('/uk/camera-traps/api/video/sign-clip', data={
        'batch_id': 'b1', 'filename': 'IMAG0001.AVI', 'start': '2025-08-13T13:09:41',
        'source': 'manual', 'step_seconds': '2'})
    assert response.status_code == 200
    body = response.get_json()
    assert body['card_index'] is None
    assert body['offsets'] == []
    with app.app_context():
        assert vu.frame_capture_time(body['token'], 'b1', 'IMAG0001.AVI', 3) == \
            datetime(2025, 8, 13, 13, 9, 41) + timedelta(seconds=6)


def test_sign_clip_refuses_an_impossible_start(auth_client):
    response = auth_client(role='admin').post('/uk/camera-traps/api/video/sign-clip', data={
        'batch_id': 'b1', 'filename': 'x.avi', 'start': '2000-01-01T00:00:00',
        'source': 'manual'})
    assert response.status_code == 400


def test_sign_clip_refuses_an_unknown_source(auth_client):
    """Times read off the strip go through read-clip, never through here."""
    response = auth_client(role='admin').post('/uk/camera-traps/api/video/sign-clip', data={
        'batch_id': 'b1', 'filename': 'x.avi', 'start': '2025-08-13T13:09:41',
        'source': 'overlay'})
    assert response.status_code == 400


def test_thumbnails_only_is_the_default_on_the_page():
    """Frames of video are stills of footage nobody keeps; full size fills the disk."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / 'app' / 'camera_traps'
            / 'templates' / 'upload_video.html').read_text(encoding='utf-8')
    select = html[html.index('id="save-original"'):]
    select = select[:select.index('</select>')]
    assert '<option value="false" selected>' in select
    assert '<option value="true" selected>' not in select
