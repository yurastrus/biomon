"""
"Пропустити" in the default mode must actually skip.

Reported 11.09.2026: on /camera-traps/identify the default mode is "Пріоритет
визначених", and pressing "Пропустити" kept returning the SAME series, so a
series you cannot identify could not be put aside.

Cause: `priority_random` ordered by `votes DESC, photo_count DESC, random()`.
Both of the first two keys sort BEFORE random(), so random() only broke ties.
Measured on production, the top rank (4 votes, 9 photos) was held by exactly one
series, so `LIMIT 1` returned it every single time. The skip button re-ran the
identical query and could not move.

Fix: two coarse tiers — identified at least once, then everything else — random
within each. That puts random() first in practice.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identify_skip.py -v
"""
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
    ct_session.add(Identification(
        photo_id=photo.id, user_id=user_id, species_id=species_id))
    ct_session.commit()


@pytest.fixture
def uneven_voted_series(ct_route_session, make_ct_observation, make_ct_photo):
    """Five identified series, deliberately uneven in both keys the old code
    sorted by. Under the old ranking exactly one of them (3 voters, 40 photos)
    outranked every other, and the queue never left it."""
    made = []
    for n_voters, photo_count in ((3, 40), (2, 12), (1, 7), (1, 3), (2, 1)):
        obs = make_ct_observation(photo_count=photo_count)
        photo = make_ct_photo(observation=obs)
        for i in range(n_voters):
            _vote(ct_route_session, photo, user_id=90 + i)
        made.append(obs)
    return made


def test_skipping_reaches_every_series_in_the_tier(auth_client, db_session,
                                                   uneven_voted_series):
    """The regression itself: repeated requests must not pin one series."""
    cl = auth_client(role='admin')
    served = set()
    for _ in range(60):
        resp = cl.get(URL)
        assert resp.status_code == 200
        served.add(resp.get_json()['observation_id'])
    assert served == {o.id for o in uneven_voted_series}, (
        'the queue is pinned to a subset - "Пропустити" cannot move off it')


def test_the_old_top_ranked_series_is_not_returned_every_time(
        auth_client, db_session, uneven_voted_series):
    """Pointed at the exact series the old ranking pinned: 3 voters, 40 photos."""
    pinned = uneven_voted_series[0].id
    cl = auth_client(role='admin')
    results = []
    for _ in range(40):
        resp = cl.get(URL)
        assert resp.status_code == 200
        results.append(resp.get_json()['observation_id'])
    assert results.count(pinned) < len(results), (
        'still returning the previously top-ranked series on every request')


def test_untouched_series_stay_behind_the_identified_ones(
        auth_client, db_session, ct_route_session, uneven_voted_series,
        make_ct_observation, make_ct_photo):
    """Shuffling inside the tier must not let the second tier leak forward."""
    untouched = make_ct_observation(photo_count=500)
    make_ct_photo(observation=untouched)

    cl = auth_client(role='admin')
    for _ in range(40):
        resp = cl.get(URL)
        assert resp.status_code == 200
        assert resp.get_json()['observation_id'] != untouched.id


def test_untouched_series_are_served_once_the_first_tier_is_exhausted(
        auth_client, db_session, ct_route_session, make_ct_observation,
        make_ct_photo):
    """With nothing identified yet, the second tier is all there is."""
    obs_a = make_ct_observation(photo_count=2)
    make_ct_photo(observation=obs_a)
    obs_b = make_ct_observation(photo_count=90)
    make_ct_photo(observation=obs_b)

    cl = auth_client(role='admin')
    served = set()
    for _ in range(40):
        resp = cl.get(URL)
        assert resp.status_code == 200
        served.add(resp.get_json()['observation_id'])
    assert served == {obs_a.id, obs_b.id}, (
        'the untouched tier must be shuffled too, not ordered by photo count')
