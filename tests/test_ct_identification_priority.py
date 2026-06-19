"""
CT identification queue (#32, analogous to Idea 7 for PAM): prioritize
series that are closer to consensus.

GET /api/next-observation-for-identification (normal mode) should serve:
  contested (>=2 votes, no winner) -> one vote -> fresh,
random within a group. The user's own votes still exclude a series.
Review mode is unaffected.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identification_priority.py -v
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/next-observation-for-identification'


@pytest.fixture
def ct_route_session(ct_session):
    """Replaces get_ct_session/close_ct_session in camera_traps.routes
    with a real SQLite session backed by CT tables."""
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
    """fresh (0 votes), one_vote (1), contested (2 votes from different users)."""
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
    """When the contested series is excluded (own vote), the next one is the one-vote series."""
    cl = auth_client(role='admin')
    _vote(ct_route_session, three_series['photo_contested'],
          user_id=_admin_id(db_session))
    resp = cl.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == three_series['one'].id


def test_own_votes_still_exclude_series(
        auth_client, db_session, three_series, ct_route_session):
    """A user's vote on any photo in a series removes it from the queue."""
    cl = auth_client(role='admin')
    admin_id = _admin_id(db_session)
    _vote(ct_route_session, three_series['photo_contested'], user_id=admin_id)
    _vote(ct_route_session, three_series['photo_one'], user_id=admin_id)
    resp = cl.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == three_series['fresh'].id


def test_review_mode_sort_unaffected(auth_client, db_session, three_series):
    """Review mode: explicit sort_by=date_desc works as before -
    the newest series with identifications (not contested-first)."""
    cl = auth_client(role='admin')
    resp = cl.get(URL + '?review=true&sort_by=date_desc')
    assert resp.status_code == 200
    # obs_one is newer (Jan 2) than contested (Jan 1) - vote priority does not apply
    assert resp.get_json()['observation_id'] == three_series['one'].id
