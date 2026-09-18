"""Tests for the server side of the camera-trap video upload.

The reading itself is covered in test_ct_video_timestamp.py. What matters here is
the boundary with the browser: a frame's capture time is the single most
consequential field in the record, and a wrong one corrupts series grouping,
activity-by-hour and phenology without anyone noticing. So the rules about who
gets to decide it are tested directly.
"""

from datetime import datetime, timedelta

import pytest

from app.camera_traps import video_timestamp as vt
from app.camera_traps import video_upload as vu

from tests.test_ct_video_timestamp import _clip, _cuddeback_card, _draw_frame


@pytest.fixture
def ctx(app):
    """The module signs tokens with the application's secret key."""
    with app.app_context():
        yield


def _jpeg(image):
    from io import BytesIO
    buf = BytesIO()
    image.convert('RGB').save(buf, format='JPEG', quality=92)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Clip tokens
# ─────────────────────────────────────────────────────────────────────────────

def test_a_frames_time_follows_from_its_index(ctx):
    start = datetime(2026, 7, 20, 14, 2, 33)
    token = vu.issue_clip_token('batch-1', 'DSCF0006.AVI', start, 1.0, None)

    assert vu.frame_capture_time(token, 'batch-1', 'DSCF0006.AVI', 0) == start
    assert vu.frame_capture_time(token, 'batch-1', 'DSCF0006.AVI', 7) == \
        start + timedelta(seconds=7)


def test_a_slower_sampling_step_stretches_the_frame_times(ctx):
    start = datetime(2026, 7, 20, 14, 2, 33)
    token = vu.issue_clip_token('batch-1', 'clip.avi', start, 5.0, None)

    assert vu.frame_capture_time(token, 'batch-1', 'clip.avi', 3) == \
        start + timedelta(seconds=15)


def test_a_token_cannot_be_replayed_against_another_clip(ctx):
    """Why the batch and file name are inside the signature.

    Without this, a token issued for a clip whose time was read correctly could
    be attached to frames of an entirely different clip, backdating them to
    whatever suited. The signature alone would still verify.
    """
    token = vu.issue_clip_token('batch-1', 'clip-a.avi',
                                datetime(2026, 7, 20, 14, 2, 33), 1.0, None)

    with pytest.raises(vu.VideoUploadError):
        vu.frame_capture_time(token, 'batch-1', 'clip-b.avi', 0)

    with pytest.raises(vu.VideoUploadError):
        vu.frame_capture_time(token, 'batch-2', 'clip-a.avi', 0)


def test_a_tampered_token_is_refused(ctx):
    token = vu.issue_clip_token('batch-1', 'clip.avi',
                                datetime(2026, 7, 20, 14, 2, 33), 1.0, None)

    with pytest.raises(vu.VideoUploadError):
        vu.frame_capture_time(token[:-4] + 'AAAA', 'batch-1', 'clip.avi', 0)


def test_a_made_up_token_is_refused(ctx):
    with pytest.raises(vu.VideoUploadError):
        vu.frame_capture_time('not-a-token', 'batch-1', 'clip.avi', 0)


def test_a_negative_frame_index_is_refused(ctx):
    # Otherwise a frame could be dated before the clip that contains it.
    token = vu.issue_clip_token('batch-1', 'clip.avi',
                                datetime(2026, 7, 20, 14, 2, 33), 1.0, None)
    with pytest.raises(vu.VideoUploadError):
        vu.frame_capture_time(token, 'batch-1', 'clip.avi', -1)


def test_the_title_card_index_travels_with_the_token(ctx):
    token = vu.issue_clip_token('batch-1', 'clip.m4v',
                                datetime(2024, 6, 20, 11, 13), 1.0, 0)
    assert vu.card_frame_index(token) == 0


def test_no_card_index_for_a_camera_that_has_no_card(ctx):
    token = vu.issue_clip_token('batch-1', 'clip.avi',
                                datetime(2026, 7, 20, 14, 2, 33), 1.0, None)
    assert vu.card_frame_index(token) is None


# ─────────────────────────────────────────────────────────────────────────────
# Calibration picks the layout by itself
# ─────────────────────────────────────────────────────────────────────────────

def test_calibration_recognises_a_bar_camera(ctx):
    start = datetime(2026, 7, 20, 14, 2, 33)
    frames = [_jpeg(f) for f in _clip(start, count=10)]

    profile = vu.calibrate([frames], [start], 'ymd', '24', 4, name='bar camera')

    assert profile.layout == vt.LAYOUT_BAR


def test_calibration_recognises_a_title_card_camera(ctx):
    """The operator is not asked to classify their camera.

    Getting the layout wrong is not a silent failure -- neither layout can be
    reconciled with the other's frames -- so there is nothing to gain from
    turning it into a question.
    """
    stamps = [datetime(2024, 6, 20, 11, 13), datetime(2024, 7, 9, 15, 51)]
    clips = [[_jpeg(_cuddeback_card(s)), _jpeg(_draw_frame('', seed=1))]
             for s in stamps]

    profile = vu.calibrate(clips, stamps, 'mdy', '12', 4, name='card camera')

    assert profile.layout == vt.LAYOUT_CARD
    assert profile.padded is False
    assert profile.has_seconds is False


def test_calibration_refuses_a_timestamp_that_contradicts_the_frames(ctx):
    start = datetime(2026, 7, 20, 14, 2, 33)
    frames = [_jpeg(f) for f in _clip(start, count=8)]

    with pytest.raises(vu.VideoUploadError):
        vu.calibrate([frames], [start + timedelta(days=1)], 'ymd', '24', 4)


def test_calibration_needs_frames(ctx):
    with pytest.raises(vu.VideoUploadError):
        vu.calibrate([], [], 'ymd', '24', 4)


def test_calibration_needs_a_timestamp_per_clip(ctx):
    start = datetime(2026, 7, 20, 14, 2, 33)
    frames = [_jpeg(f) for f in _clip(start, count=4)]

    with pytest.raises(vu.VideoUploadError):
        vu.calibrate([frames, frames], [start], 'ymd', '24', 4)


# ─────────────────────────────────────────────────────────────────────────────
# Reading a clip for the page
# ─────────────────────────────────────────────────────────────────────────────

def test_reading_reports_the_strip_it_read_from(ctx):
    """The page shows this strip next to the parsed date.

    It is the only thing standing between a wrong date order and a year of
    quietly misdated records, so its absence would not be a cosmetic loss.
    """
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vu.calibrate([[_jpeg(f) for f in _clip(start, count=10)]],
                           [start], 'ymd', '24', 4)

    other = datetime(2026, 9, 8, 6, 35, 25)
    result = vu.read_clip([_jpeg(f) for f in _clip(other, count=8)],
                          profile, 'DSCF0042.AVI')

    assert result['start'] == other.isoformat()
    assert result['confident'] is True
    assert result['strip_png']


def test_reading_reports_a_counter_that_does_not_match_the_file(ctx):
    # A free cross-check: the number the camera prints should be the number in
    # the file name. A mismatch does not block the upload, but the operator is
    # told, because it usually means the frames and the file drifted apart.
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vu.calibrate([[_jpeg(f) for f in _clip(start, count=10)]],
                           [start], 'ymd', '24', 4)

    result = vu.read_clip([_jpeg(f) for f in _clip(start, count=6)],
                          profile, 'DSCF9999.AVI')
    assert result['counter_matches'] is False


def test_an_unreadable_clip_is_reported_not_guessed(ctx):
    start = datetime(2026, 7, 20, 14, 2, 33)
    profile = vu.calibrate([[_jpeg(f) for f in _clip(start, count=10)]],
                           [start], 'ymd', '24', 4)

    blank = [_jpeg(_draw_frame('', seed=i)) for i in range(4)]
    with pytest.raises(vu.VideoUploadError):
        vu.read_clip(blank, profile, 'clip.avi')


def test_reading_an_empty_clip_is_refused(ctx):
    with pytest.raises(vu.VideoUploadError):
        vu.read_clip([], vt.CameraProfile(), 'clip.avi')
