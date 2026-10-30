"""A series flagged for re-review keeps its photos until an admin unflags it.

Before 2026-10-30 the nightly cleanup archived flagged series like any other
(two of the three flagged series on prod had lost every photo), and any
verifier could remove the flag. Now:
  * cleanup_old_photos and its admin estimate skip flagged series;
  * the "delete raw files of all non-favourites" button skips them too;
  * only an admin may remove the flag.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def env(app, ct_session, tmp_path, monkeypatch):
    from app.camera_traps import background_tasks as bt
    raw = tmp_path / 'pending_photos' / 'raw'
    thumb = tmp_path / 'pending_photos' / 'thumbnails'
    raw.mkdir(parents=True)
    thumb.mkdir(parents=True)
    monkeypatch.setattr(bt, 'get_ct_session', lambda: ct_session)
    monkeypatch.setattr(bt, 'close_ct_session', lambda: None)
    monkeypatch.setitem(app.config, 'CAMERA_TRAP_CONFIG',
                        {'UPLOAD_PATH': str(tmp_path), 'CLEANUP_DAYS': 1,
                         'MIN_IDENTIFICATIONS': 2})
    with app.app_context():
        yield raw, thumb


def _old_series(ct_session, make_ct_observation, make_ct_photo, species, name, flagged):
    from app.camera_traps.models import Identification
    now = datetime.utcnow()
    obs = make_ct_observation(status='completed', flagged=flagged,
                              flag_note='дві єнотовидки' if flagged else None)
    photo = make_ct_photo(observation=obs, status='completed', is_favorite=False,
                          system_filename=name)
    ct_session.add(Identification(photo_id=photo.id, user_id=1, species_id=species.id,
                                  created_at=now - timedelta(days=10)))
    ct_session.commit()
    return obs, photo


def _files(raw, thumb, name):
    (raw / name).write_bytes(b'r' * 100)
    (thumb / name).write_bytes(b't' * 10)


def test_nightly_cleanup_keeps_flagged_series(env, ct_session, make_ct_observation,
                                              make_ct_photo, make_ct_species):
    from app.camera_traps.background_tasks import cleanup_old_photos
    raw, thumb = env
    sp = make_ct_species()
    flagged, fphoto = _old_series(ct_session, make_ct_observation, make_ct_photo, sp, 'f.jpg', True)
    plain, pphoto = _old_series(ct_session, make_ct_observation, make_ct_photo, sp, 'p.jpg', False)
    _files(raw, thumb, 'f.jpg')
    _files(raw, thumb, 'p.jpg')

    result = cleanup_old_photos()

    assert result['success'] and result['observations_archived'] == 1
    ct_session.expire_all()
    assert (flagged.status, fphoto.status) == ('completed', 'completed')
    assert (raw / 'f.jpg').exists() and (thumb / 'f.jpg').exists()
    assert (plain.status, pphoto.status) == ('archived', 'archived')
    assert not (raw / 'p.jpg').exists()


def test_admin_estimate_matches_the_cleanup(env, ct_session, make_ct_observation,
                                            make_ct_photo, make_ct_species):
    from app.camera_traps.background_tasks import get_cleanup_statistics
    raw, thumb = env
    sp = make_ct_species()
    _old_series(ct_session, make_ct_observation, make_ct_photo, sp, 'f.jpg', True)
    _files(raw, thumb, 'f.jpg')
    stats = get_cleanup_statistics()
    assert stats['observations_count'] == 0 and stats['photos_count'] == 0


def test_raw_purge_button_skips_flagged_series(env, ct_session, make_ct_observation,
                                               make_ct_photo, make_ct_species):
    from app.camera_traps.background_tasks import delete_unfavorited_raw_files
    raw, thumb = env
    sp = make_ct_species()
    _old_series(ct_session, make_ct_observation, make_ct_photo, sp, 'f.jpg', True)
    _old_series(ct_session, make_ct_observation, make_ct_photo, sp, 'p.jpg', False)
    _files(raw, thumb, 'f.jpg')
    _files(raw, thumb, 'p.jpg')
    delete_unfavorited_raw_files()
    assert (raw / 'f.jpg').exists()
    assert not (raw / 'p.jpg').exists()


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.mark.parametrize('role,cleared', [('ct_verifier', False), ('manager', False),
                                          ('admin', True)])
def test_only_admin_removes_the_flag(auth_client, db_session, ct_route_session,
                                     make_ct_observation, role, cleared):
    obs = make_ct_observation(flagged=True, flag_note='x')
    cl = auth_client(role=role)
    cl.post(f'/uk/camera-traps/observation/{obs.id}/unflag', data={})
    ct_route_session.refresh(obs)
    assert obs.flagged is (not cleared)


# ── "Other species" keeps a series only when it is the consensus ─────────────

def _series_with_votes(ct_session, make_ct_observation, make_ct_photo, name, votes):
    from app.camera_traps.models import Identification
    obs = make_ct_observation(status='completed')
    photo = make_ct_photo(observation=obs, status='completed', is_favorite=False,
                          system_filename=name)
    old = datetime.utcnow() - timedelta(days=10)
    for uid, sp in votes:
        ct_session.add(Identification(photo_id=photo.id, user_id=uid, species_id=sp,
                                      created_at=old))
    ct_session.commit()
    return obs


@pytest.fixture
def other_species(ct_session):
    from app.camera_traps.models import Species
    roe = Species(id=4, scientific_name='Capreolus capreolus', category='mammals',
                  common_name_ua='Козуля')
    other = Species(id=-2, scientific_name='other', category='other',
                    common_name_ua='Інший вид')
    ct_session.add_all([roe, other])
    ct_session.commit()
    return roe.id, other.id


def test_lone_other_vote_no_longer_keeps_a_roe_deer_series(
        env, ct_session, make_ct_observation, make_ct_photo, other_species):
    from app.camera_traps.background_tasks import cleanup_old_photos
    raw, thumb = env
    roe, other = other_species
    minority = _series_with_votes(ct_session, make_ct_observation, make_ct_photo, 'm.jpg',
                                  [(1, other), (2, roe), (3, roe)])
    consensus = _series_with_votes(ct_session, make_ct_observation, make_ct_photo, 'c.jpg',
                                   [(1, other), (2, other), (3, roe)])
    _files(raw, thumb, 'm.jpg')
    _files(raw, thumb, 'c.jpg')

    cleanup_old_photos()

    ct_session.expire_all()
    assert minority.status == 'archived' and not (raw / 'm.jpg').exists()
    assert consensus.status == 'completed' and (raw / 'c.jpg').exists()


def test_estimate_follows_the_same_other_rule(
        env, ct_session, make_ct_observation, make_ct_photo, other_species):
    from app.camera_traps.background_tasks import get_cleanup_statistics
    raw, thumb = env
    roe, other = other_species
    _series_with_votes(ct_session, make_ct_observation, make_ct_photo, 'm.jpg',
                       [(1, other), (2, roe), (3, roe)])
    _series_with_votes(ct_session, make_ct_observation, make_ct_photo, 'c.jpg',
                       [(1, other), (2, other), (3, roe)])
    _files(raw, thumb, 'm.jpg')
    _files(raw, thumb, 'c.jpg')
    assert get_cleanup_statistics()['observations_count'] == 1


def test_unidentifiable_with_few_verifiers_is_kept(env, ct_session, make_ct_observation,
                                                   make_ct_photo):
    from app.camera_traps.models import Species
    from app.camera_traps.background_tasks import cleanup_old_photos
    raw, thumb = env
    ct_session.add(Species(id=-6, scientific_name='not identifiable', category='other',
                           common_name_ua='Неможливо визначити'))
    ct_session.commit()
    few = _series_with_votes(ct_session, make_ct_observation, make_ct_photo, 'few.jpg',
                             [(1, -6), (2, -6), (3, -6)])
    five = _series_with_votes(ct_session, make_ct_observation, make_ct_photo, 'five.jpg',
                              [(u, -6) for u in range(1, 6)])
    _files(raw, thumb, 'few.jpg')
    _files(raw, thumb, 'five.jpg')
    cleanup_old_photos()
    ct_session.expire_all()
    assert few.status == 'completed' and (raw / 'few.jpg').exists()
    assert five.status == 'archived' and not (raw / 'five.jpg').exists()
