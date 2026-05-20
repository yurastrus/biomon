"""
Тести допоміжних функцій модуля camera_traps.

Покриває:
  - extract_datetime_from_exif: валідний / невалідний потік
  - mark_observation_complete: status переходить у 'completed'
  - check_consensus_for_observation: 3 голоси однакові → completed
"""
import io
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.camera_traps.utils import (
    extract_datetime_from_exif,
    mark_observation_complete,
    check_consensus_for_observation,
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
                                      species_id=species.id,
                                      confidence_level=4))
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
                                  species_id=species.id, confidence_level=3))
    ct_session.commit()

    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'MIN_IDENTIFICATIONS': 3}
        check_consensus_for_observation(obs.id, db_session=ct_session)

    ct_session.refresh(obs)
    assert obs.status == 'pending'
