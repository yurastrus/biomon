"""
Default /identify queue ('priority_random'), three tiers since 2026-11-19:
series with an animal vote first, then series with only 'Empty' votes, then
untouched ones. Random inside each tier.

Why: with two tiers, 77% of the voted tier on production carried a single
'Empty' vote, so the priority mostly served empty frames.
"""
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/next-observation-for-identification'
EMPTY = -1


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _series(ct_session, make_ct_observation, make_ct_photo, votes=()):
    from app.camera_traps.models import Identification
    obs = make_ct_observation()
    photo = make_ct_photo(observation=obs)
    for user_id, species_id in votes:
        ct_session.add(Identification(photo_id=photo.id, user_id=user_id,
                                      species_id=species_id))
    ct_session.commit()
    return obs


@pytest.fixture
def three_series(ct_route_session, make_ct_observation, make_ct_photo, make_ct_species):
    fox = make_ct_species()
    untouched = _series(ct_route_session, make_ct_observation, make_ct_photo)
    empty = _series(ct_route_session, make_ct_observation, make_ct_photo,
                    votes=[(91, EMPTY)])
    animal = _series(ct_route_session, make_ct_observation, make_ct_photo,
                     votes=[(92, fox.id)])
    return animal, empty, untouched


def test_animal_vote_comes_before_empty_vote(auth_client, db_session, three_series):
    animal, _empty, _untouched = three_series
    cl = auth_client(role='admin')
    for _ in range(10):
        assert cl.get(URL).get_json()['observation_id'] == animal.id


def test_empty_vote_comes_before_untouched(auth_client, db_session, three_series):
    animal, empty, _untouched = three_series
    cl = auth_client(role='admin')
    for _ in range(10):
        resp = cl.get(URL + f'?exclude_obs_id={animal.id}')
        assert resp.get_json()['observation_id'] == empty.id


def test_untouched_served_when_voted_tiers_are_done(auth_client, db_session, three_series):
    animal, empty, untouched = three_series
    cl = auth_client(role='admin')
    resp = cl.get(URL + f'?exclude_obs_id={animal.id}&exclude_obs_id={empty.id}')
    assert resp.get_json()['observation_id'] == untouched.id


def test_disagreement_with_empty_counts_as_animal_tier(
        auth_client, db_session, ct_route_session, make_ct_observation,
        make_ct_photo, make_ct_species):
    """One 'Empty' and one animal vote: the series needs a tie-breaker, so it
    belongs with the animal tier, ahead of a plain 'Empty' series."""
    fox = make_ct_species()
    _series(ct_route_session, make_ct_observation, make_ct_photo, votes=[(91, EMPTY)])
    split = _series(ct_route_session, make_ct_observation, make_ct_photo,
                    votes=[(91, EMPTY), (92, fox.id)])
    cl = auth_client(role='admin')
    for _ in range(10):
        assert cl.get(URL).get_json()['observation_id'] == split.id


def test_animal_tier_is_shuffled(auth_client, db_session, ct_route_session,
                                 make_ct_observation, make_ct_photo, make_ct_species):
    """Random inside the tier, so "Пропустити" can move between series."""
    fox = make_ct_species()
    made = {_series(ct_route_session, make_ct_observation, make_ct_photo,
                    votes=[(90 + i, fox.id)]).id for i in range(3)}
    _series(ct_route_session, make_ct_observation, make_ct_photo, votes=[(95, EMPTY)])
    cl = auth_client(role='admin')
    served = {cl.get(URL).get_json()['observation_id'] for _ in range(40)}
    assert served == made
