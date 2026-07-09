"""
Tests for camera_traps helper functions.

Cover:
  - extract_datetime_from_exif: valid / invalid stream
  - mark_observation_complete: status moves to 'completed'
  - check_consensus_for_observation: 3 identical votes → completed
"""
import io
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.camera_traps.utils import (
    extract_datetime_from_exif,
    mark_observation_complete,
    check_consensus_for_observation,
    create_thumbnail,
    _verify_files_on_disk,
)
from app.camera_traps.models import Identification


def test_extract_datetime_from_exif_invalid_returns_none():
    stream = io.BytesIO(b'not a real image')
    assert extract_datetime_from_exif(stream) is None


def test_extract_datetime_from_exif_with_mocked_tags(monkeypatch):
    fake_tags = {'EXIF DateTimeOriginal': '2025:03:15 14:23:45'}
    monkeypatch.setattr('app.camera_traps.utils.exifread.process_file',
                        lambda *a, **kw: fake_tags)
    result = extract_datetime_from_exif(io.BytesIO(b'fake'))
    assert result == datetime(2025, 3, 15, 14, 23, 45)


# ── Idea 1: sanity guard against implausible EXIF dates ─────────────────────

def _mock_exif_date(monkeypatch, date_str):
    monkeypatch.setattr('app.camera_traps.utils.exifread.process_file',
                        lambda *a, **kw: {'EXIF DateTimeOriginal': date_str})


def test_extract_datetime_pre_2010_treated_as_missing(app, monkeypatch):
    """Reset camera clock (year 2000) → None → placeholder path."""
    _mock_exif_date(monkeypatch, '2000:01:01 12:00:00')
    with app.app_context():
        assert extract_datetime_from_exif(io.BytesIO(b'fake')) is None


def test_extract_datetime_far_future_treated_as_missing(app, monkeypatch):
    """A future date (> +24 h) → None."""
    future = datetime.now() + timedelta(days=3)
    _mock_exif_date(monkeypatch, future.strftime('%Y:%m:%d %H:%M:%S'))
    with app.app_context():
        assert extract_datetime_from_exif(io.BytesIO(b'fake')) is None


def test_extract_datetime_near_future_within_drift_is_valid(monkeypatch):
    """A small forward clock drift (+1 h) — acceptable."""
    near = datetime.now() + timedelta(hours=1)
    _mock_exif_date(monkeypatch, near.strftime('%Y:%m:%d %H:%M:%S'))
    result = extract_datetime_from_exif(io.BytesIO(b'fake'))
    assert result is not None
    assert abs((result - near).total_seconds()) < 1


def test_extract_datetime_min_boundary_is_valid(monkeypatch):
    """Exactly 2010-01-01 00:00:00 — still valid (boundary is inclusive)."""
    _mock_exif_date(monkeypatch, '2010:01:01 00:00:00')
    assert extract_datetime_from_exif(io.BytesIO(b'fake')) == datetime(2010, 1, 1)


def test_mark_observation_complete_changes_status(app, ct_session,
                                                   make_ct_photo):
    photo = make_ct_photo()
    obs = photo.observation
    assert obs.status == 'pending'

    with app.app_context():
        mark_observation_complete(obs.id, db_session=ct_session)
        ct_session.commit()

    ct_session.refresh(obs)
    ct_session.refresh(photo)
    assert obs.status == 'completed'
    assert photo.status == 'completed'


def test_check_consensus_with_three_matching_votes(app, ct_session,
                                                    make_ct_photo,
                                                    make_ct_species):
    photo = make_ct_photo()
    obs = photo.observation
    species = make_ct_species()

    for uid in (1, 2, 3):
        ct_session.add(Identification(photo_id=photo.id, user_id=uid,
                                      species_id=species.id))
    ct_session.commit()

    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'MIN_IDENTIFICATIONS': 2}
        check_consensus_for_observation(obs.id, db_session=ct_session)
        ct_session.commit()

    ct_session.refresh(obs)
    assert obs.status == 'completed'


def test_check_consensus_insufficient_votes_stays_pending(app, ct_session,
                                                          make_ct_photo,
                                                          make_ct_species):
    photo = make_ct_photo()
    obs = photo.observation
    species = make_ct_species()

    ct_session.add(Identification(photo_id=photo.id, user_id=1,
                                  species_id=species.id))
    ct_session.commit()

    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'MIN_IDENTIFICATIONS': 3}
        check_consensus_for_observation(obs.id, db_session=ct_session)

    ct_session.refresh(obs)
    assert obs.status == 'pending'


# ── Disk-full guard: a Photo row must never exist without its file ──────────
# Regression for the 2026-07-08 disk-full incident: files failed to write but
# DB rows were still created → orphans that jam AI classification forever.

def _tiny_jpeg_stream():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), (120, 30, 30)).save(buf, 'JPEG')
    buf.seek(0)
    return buf


def test_verify_files_on_disk_passes_when_present_and_nonempty(tmp_path):
    p = tmp_path / 'a.jpg'
    p.write_bytes(b'\xff\xd8\xff\xe0somedata')
    _verify_files_on_disk([str(p)])  # must not raise


def test_verify_files_on_disk_raises_when_missing(tmp_path):
    with pytest.raises(IOError):
        _verify_files_on_disk([str(tmp_path / 'does_not_exist.jpg')])


def test_verify_files_on_disk_raises_when_empty(tmp_path):
    """A 0-byte file (typical disk-full partial write) counts as not-saved."""
    empty = tmp_path / 'empty.jpg'
    empty.write_bytes(b'')
    with pytest.raises(IOError):
        _verify_files_on_disk([str(empty)])


def test_verify_files_on_disk_raises_if_any_of_several_missing(tmp_path):
    good = tmp_path / 'good.jpg'
    good.write_bytes(b'data')
    with pytest.raises(IOError):
        _verify_files_on_disk([str(good), str(tmp_path / 'missing.jpg')])


def test_create_thumbnail_returns_true_and_writes_file(app, tmp_path):
    with app.app_context(), \
         patch.dict(app.config, {'CAMERA_TRAP_CONFIG': {'THUMBNAIL_SIZE': (64, 64)}}):
        out = tmp_path / 'thumb.jpg'
        ok = create_thumbnail(_tiny_jpeg_stream(), str(out))
    assert ok is True
    assert out.exists() and out.stat().st_size > 0


def test_create_thumbnail_returns_false_on_bad_source(app, tmp_path):
    with app.app_context(), \
         patch.dict(app.config, {'CAMERA_TRAP_CONFIG': {'THUMBNAIL_SIZE': (64, 64)}}):
        out = tmp_path / 'thumb.jpg'
        ok = create_thumbnail(io.BytesIO(b'not an image at all'), str(out))
    assert ok is False
    assert not out.exists()


def test_create_thumbnail_returns_false_on_write_failure(app, tmp_path):
    """Destination directory missing → write fails (stands in for disk-full).
    Must report False (not swallow-and-continue) so the caller aborts."""
    with app.app_context(), \
         patch.dict(app.config, {'CAMERA_TRAP_CONFIG': {'THUMBNAIL_SIZE': (64, 64)}}):
        out = tmp_path / 'no_such_dir' / 'thumb.jpg'  # parent does not exist
        ok = create_thumbnail(_tiny_jpeg_stream(), str(out))
    assert ok is False
