"""
CT identify: sort options available to ALL users (not just moderators),
and 'priority_random' additionally ranks already-voted series by photo count.

GET /api/next-observation-for-identification

Covers:
  - normal mode honors every sort_by value, not just the hardcoded priority
    logic (previously sort_by was silently ignored outside review mode);
  - review mode gains 'priority_random' as a real option (previously any
    non-recognized value, including the old default, fell through to plain
    'random');
  - 'priority_random' (the shared default) ranks series with more photos
    first among those that already have votes, and leaves untouched
    (0-vote) series unaffected by photo count;
  - an unknown sort_by value falls back to 'priority_random' rather than
    erroring.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/next-observation-for-identification'


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _vote(ct_session, photo, user_id, species_id=None):
    from app.camera_traps.models import Identification
    ct_session.add(Identification(photo_id=photo.id, user_id=user_id, species_id=species_id))
    ct_session.commit()


def test_normal_mode_date_desc_is_honored(auth_client, db_session, ct_route_session,
                                          make_ct_observation, make_ct_photo):
    """Previously sort_by was ignored outside review mode; now it must apply."""
    obs_old = make_ct_observation(series_start_time=datetime(2025, 1, 1, 8, 0))
    make_ct_photo(observation=obs_old)
    obs_new = make_ct_observation(series_start_time=datetime(2025, 6, 1, 8, 0))
    make_ct_photo(observation=obs_new)

    cl = auth_client(role='admin')
    resp = cl.get(URL + '?sort_by=date_desc')
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == obs_new.id


def test_normal_mode_photo_count_desc_is_honored(auth_client, db_session, ct_route_session,
                                                 make_ct_observation, make_ct_photo):
    obs_small = make_ct_observation(photo_count=1)
    make_ct_photo(observation=obs_small)
    obs_big = make_ct_observation(photo_count=20)
    make_ct_photo(observation=obs_big)

    cl = auth_client(role='admin')
    resp = cl.get(URL + '?sort_by=photo_count_desc')
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == obs_big.id


def test_priority_random_ranks_voted_series_by_photo_count(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo):
    """Among already-voted series, the one with MORE photos comes first."""
    obs_voted_small = make_ct_observation(photo_count=2)
    photo_voted_small = make_ct_photo(observation=obs_voted_small)
    _vote(ct_route_session, photo_voted_small, user_id=97)

    obs_voted_big = make_ct_observation(photo_count=30)
    photo_voted_big = make_ct_photo(observation=obs_voted_big)
    _vote(ct_route_session, photo_voted_big, user_id=98)

    cl = auth_client(role='admin')
    resp = cl.get(URL)  # default sort_by = priority_random
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == obs_voted_big.id


def test_priority_random_still_prefers_any_vote_over_untouched_big_series(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo):
    """A big untouched (0-vote) series must not outrank a small voted one —
    the photo-count tiebreak only applies within the already-voted tier."""
    obs_untouched_big = make_ct_observation(photo_count=100)
    make_ct_photo(observation=obs_untouched_big)

    obs_voted_small = make_ct_observation(photo_count=1)
    photo_voted_small = make_ct_photo(observation=obs_voted_small)
    _vote(ct_route_session, photo_voted_small, user_id=97)

    cl = auth_client(role='admin')
    resp = cl.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == obs_voted_small.id


def test_review_mode_defaults_to_priority_random(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo):
    """Review mode with no explicit sort_by now uses the shared default
    (priority_random) instead of silently falling back to plain random."""
    obs_small = make_ct_observation(status='completed', photo_count=1)
    photo_small = make_ct_photo(observation=obs_small, status='completed')
    _vote(ct_route_session, photo_small, user_id=97)

    obs_big = make_ct_observation(status='completed', photo_count=50)
    photo_big = make_ct_photo(observation=obs_big, status='completed')
    _vote(ct_route_session, photo_big, user_id=98)

    cl = auth_client(role='admin')  # admin also has manager-equivalent review access
    resp = cl.get(URL + '?review=true')
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == obs_big.id


def test_review_mode_photo_count_asc_is_honored(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo):
    obs_small = make_ct_observation(status='completed', photo_count=1)
    photo_small = make_ct_photo(observation=obs_small, status='completed')
    _vote(ct_route_session, photo_small, user_id=97)

    obs_big = make_ct_observation(status='completed', photo_count=50)
    photo_big = make_ct_photo(observation=obs_big, status='completed')
    _vote(ct_route_session, photo_big, user_id=98)

    cl = auth_client(role='admin')
    resp = cl.get(URL + '?review=true&sort_by=photo_count_asc')
    assert resp.status_code == 200
    assert resp.get_json()['observation_id'] == obs_small.id


def test_unknown_sort_by_falls_back_to_priority_random(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo):
    obs_voted = make_ct_observation(photo_count=5)
    photo_voted = make_ct_photo(observation=obs_voted)
    _vote(ct_route_session, photo_voted, user_id=97)

    obs_untouched = make_ct_observation(photo_count=5)
    make_ct_photo(observation=obs_untouched)

    cl = auth_client(role='admin')
    resp = cl.get(URL + '?sort_by=not-a-real-option')
    assert resp.status_code == 200
    # priority_random: the voted series still wins over the untouched one.
    assert resp.get_json()['observation_id'] == obs_voted.id
