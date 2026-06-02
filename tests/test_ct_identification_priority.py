"""
CT identification queue (#32, аналог Idea 7 для PAM): пріоритет серій,
ближчих до консенсусу.

GET /api/next-observation-for-identification (normal mode) має видавати:
  спірні (≥2 голоси, без переможця) → з одним голосом → свіжі,
всередині групи — випадково. Власні голоси користувача й надалі
виключають серію. Review mode не зачеплено.

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_identification_priority.py -v
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/next-observation-for-identification'


@pytest.fixture
def ct_route_session(ct_session):
    """Підміняє get_ct_session/close_ct_session у camera_traps.routes
    на справжню SQLite-сесію з CT-таблицями."""
    with patch('app.camera_traps.routes.get_ct_session',
               return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _vote(ct_session, photo, user_id, species_id=None):
    from app.camera_traps.models import Identification
    ct_session.add(Identification(
        photo_id=photo.id, user_id=user_id, species_id=species_id))
    ct_session.commit()


@pytest.fixture
def three_series(ct_route_session, make_ct_observation, make_ct_photo):
    """fresh (0 голосів), one_vote (1), contested (2 голоси різних юзерів)."""
    obs_fresh = make_ct_observation(
        series_start_time=datetime(2025, 1, 1, 8, 0))
    photo_fresh = make_ct_photo(observation=obs_fresh)

    obs_one = make_ct_observation(
        series_start_time=datetime(2025, 1, 2, 8, 0))
    photo_one = make_ct_photo(observation=obs_one)
    _vote(ct_route_session, photo_one, user_id=97, species_id=None)

    obs_contested = make_ct_observation(
        series_start_time=datetime(2025, 1, 1, 6, 0))
    photo_contested = make_ct_photo(observation=obs_contested)
    _vote(ct_route_session, photo_contested, user_id=98, species_id=None)
    _vote(ct_route_session, photo_contested, user_id=99, species_id=None)

    return {
        'fresh': obs_fresh, 'one': obs_one, 'contested': obs_contested,
        'photo_contested': photo_contested, 'photo_one': photo_one,
    }


def _admin_id(db_session):
    from app.models import User
    return db_session.query(User).filter_by(username='test_admin').first().id


def test_contested_series_served_first(auth_client, db_session, three_series):
    cl = auth_client(role='admin')
    resp = cl.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == three_series['contested'].id


def test_one_vote_series_served_after_contested_resolved(
        auth_client, db_session, three_series, ct_route_session):
    """Коли спірна серія виключена (свій голос) — наступна та, що з 1 голосом."""
    cl = auth_client(role='admin')
    _vote(ct_route_session, three_series['photo_contested'],
          user_id=_admin_id(db_session))
    resp = cl.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == three_series['one'].id


def test_own_votes_still_exclude_series(
        auth_client, db_session, three_series, ct_route_session):
    """Голос користувача на будь-якому фото серії прибирає її з черги."""
    cl = auth_client(role='admin')
    admin_id = _admin_id(db_session)
    _vote(ct_route_session, three_series['photo_contested'], user_id=admin_id)
    _vote(ct_route_session, three_series['photo_one'], user_id=admin_id)
    resp = cl.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == three_series['fresh'].id


def test_review_mode_sort_unaffected(auth_client, db_session, three_series):
    """Review mode: явний sort_by=date_desc працює як раніше —
    найновіша серія з ідентифікаціями (а не contested-перша)."""
    cl = auth_client(role='admin')
    resp = cl.get(URL + '?review=true&sort_by=date_desc')
    assert resp.status_code == 200
    # obs_one новіша (02.01) за contested (01.01) — пріоритет голосів не діє
    assert resp.get_json()['observation_id'] == three_series['one'].id
