"""
CT admin: "estimated free space" storage estimate was overstated ~10x.

get_cleanup_statistics() must:
  - select the SAME observations cleanup_old_photos() would actually archive
    (eligibility based on the most recent identification being old enough,
    not on series_end_time, which can predate identification by a long
    delay and previously caused many not-yet-eligible series to be counted);
  - measure size from real files on disk (raw + thumbnail), not a flat
    per-photo guess.
"""
from datetime import datetime, timedelta

import pytest

from app.camera_traps.background_tasks import get_cleanup_statistics


@pytest.fixture
def ct_stats_env(app, ct_session, tmp_path, monkeypatch):
    from app.camera_traps import background_tasks as bt

    raw_dir = tmp_path / 'pending_photos' / 'raw'
    thumb_dir = tmp_path / 'pending_photos' / 'thumbnails'
    raw_dir.mkdir(parents=True)
    thumb_dir.mkdir(parents=True)

    monkeypatch.setattr(bt, 'get_ct_session', lambda: ct_session)
    monkeypatch.setattr(bt, 'close_ct_session', lambda: None)

    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {
            'UPLOAD_PATH': str(tmp_path),
            'CLEANUP_DAYS': 30,
        }
        yield raw_dir, thumb_dir


def _write(path, directory, size_bytes):
    (directory / path).write_bytes(b'0' * size_bytes)


def _mk_series(ct_session, make_ct_observation, make_ct_photo, make_ct_species,
               *, series_age_days, identified_days_ago, filename, species_id=None):
    from app.camera_traps.models import Identification

    sp = make_ct_species() if species_id is None else None
    now = datetime.utcnow()
    obs = make_ct_observation(
        status='completed',
        series_start_time=now - timedelta(days=series_age_days, minutes=5),
        series_end_time=now - timedelta(days=series_age_days),
    )
    photo = make_ct_photo(
        observation=obs, status='completed', is_favorite=False,
        system_filename=filename,
    )
    ident = Identification(
        photo_id=photo.id,
        user_id=1,
        species_id=species_id if species_id is not None else sp.id,
        created_at=now - timedelta(days=identified_days_ago),
    )
    ct_session.add(ident)
    ct_session.commit()
    return obs, photo


def test_uses_identification_recency_not_series_end_time(
        ct_stats_env, ct_session, make_ct_observation, make_ct_photo, make_ct_species):
    """A series captured long ago but identified recently is NOT yet eligible —
    matching cleanup_old_photos()'s real criterion, unlike the old
    series_end_time-based query."""
    raw_dir, thumb_dir = ct_stats_env
    _write('recent_id.jpg', raw_dir, 1000)
    _write('recent_id.jpg', thumb_dir, 100)

    _mk_series(ct_session, make_ct_observation, make_ct_photo, make_ct_species,
               series_age_days=90, identified_days_ago=1, filename='recent_id.jpg')

    stats = get_cleanup_statistics()
    assert stats['observations_count'] == 0
    assert stats['photos_count'] == 0
    assert stats['estimated_size_mb'] == 0


def test_old_identification_is_eligible(
        ct_stats_env, ct_session, make_ct_observation, make_ct_photo, make_ct_species):
    raw_dir, thumb_dir = ct_stats_env
    _write('old_id.jpg', raw_dir, 1000)
    _write('old_id.jpg', thumb_dir, 100)

    _mk_series(ct_session, make_ct_observation, make_ct_photo, make_ct_species,
               series_age_days=5, identified_days_ago=45, filename='old_id.jpg')

    stats = get_cleanup_statistics()
    assert stats['observations_count'] == 1
    assert stats['photos_count'] == 1


def test_size_measured_from_real_files_not_flat_guess(
        ct_stats_env, ct_session, make_ct_observation, make_ct_photo, make_ct_species):
    raw_dir, thumb_dir = ct_stats_env
    # 3 MiB raw + 0.5 MiB thumbnail == 3.5 MiB, deliberately not the old flat "2 MB" guess.
    _write('big.jpg', raw_dir, 3 * 1024 * 1024)
    _write('big.jpg', thumb_dir, 512 * 1024)

    _mk_series(ct_session, make_ct_observation, make_ct_photo, make_ct_species,
               series_age_days=5, identified_days_ago=45, filename='big.jpg')

    stats = get_cleanup_statistics()
    assert stats['estimated_size_mb'] == pytest.approx(3.5, abs=0.01)


def test_species_other_excludes_series_from_archival_and_estimate(
        ct_stats_env, ct_session, make_ct_observation, make_ct_photo, make_ct_species):
    raw_dir, thumb_dir = ct_stats_env
    _write('other.jpg', raw_dir, 1000)
    _write('other.jpg', thumb_dir, 100)

    from app.camera_traps.models import Species
    if not ct_session.get(Species, -2):
        ct_session.add(Species(id=-2, scientific_name='other', category='other'))
        ct_session.commit()

    _mk_series(ct_session, make_ct_observation, make_ct_photo, make_ct_species,
               series_age_days=5, identified_days_ago=45, filename='other.jpg',
               species_id=-2)

    stats = get_cleanup_statistics()
    assert stats['observations_count'] == 0


def test_missing_file_on_disk_contributes_zero_not_crash(
        ct_stats_env, ct_session, make_ct_observation, make_ct_photo, make_ct_species):
    _mk_series(ct_session, make_ct_observation, make_ct_photo, make_ct_species,
               series_age_days=5, identified_days_ago=45, filename='gone.jpg')

    stats = get_cleanup_statistics()
    assert stats['observations_count'] == 1
    assert stats['estimated_size_mb'] == 0
