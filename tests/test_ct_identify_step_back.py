"""Stepping back on /identify to correct an answer that was already submitted.

A verifier who has just submitted a series can reopen it (last 3 only, enforced
by the page) and submit again. With `replace: true` the caller's own rows are
swapped for the new ones; without it the old insert-only behaviour is kept.
Consensus runs for real here (3 votes, majority > 50%), only the DB session is
swapped for the in-memory SQLite one.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/submit-identification'
NEXT_URL = '/uk/camera-traps/api/next-observation-for-identification'


@pytest.fixture
def ct(ct_session):
    from app.camera_traps import models as m
    m.identification_behaviors.create(bind=ct_session.get_bind(), checkfirst=True)
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.fixture
def series(ct, make_ct_location, make_ct_observation, make_ct_photo):
    """A two-photo series on a public location (a plain verifier may vote on it)."""
    loc = make_ct_location(name='Публічна', visibility_level=0)
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs, captured_at=datetime(2025, 1, 1, 12, 0))
    make_ct_photo(observation=obs, captured_at=datetime(2025, 1, 1, 12, 1))
    return obs


@pytest.fixture
def species(make_ct_species):
    return (make_ct_species(scientific_name='Vulpes vulpes'),
            make_ct_species(scientific_name='Capreolus capreolus',
                            common_name_ua='Козуля', common_name_en='Roe deer'),
            make_ct_species(scientific_name='Sus scrofa',
                            common_name_ua='Кабан', common_name_en='Boar'))


@pytest.fixture
def voters(auth_client):
    return [auth_client(role='ct_verifier', username=f'verifier_{i}') for i in range(3)]


def _fresh_user():
    """The app fixture keeps one app context open for the whole test, so
    Flask-Login's per-context user cache would make a second client act as the
    first. Drop it before every request."""
    from flask import g
    g.pop('_login_user', None)


def _vote(cl, obs, species_id, **extra):
    _fresh_user()
    payload = {'observation_id': obs.id, 'species_id': species_id, 'quantity': 1}
    payload.update(extra)
    return cl.post(URL, json=payload)


def _rows(ct, obs):
    from app.camera_traps.models import Identification, Photo
    return (ct.query(Identification).join(Photo)
            .filter(Photo.observation_id == obs.id)
            .order_by(Identification.id).all())


def _mine(ct, obs, user_id):
    return [i for i in _rows(ct, obs) if i.user_id == user_id]


def _uid(cl):
    with cl.session_transaction() as sess:
        return int(sess['_user_id'])


def test_replace_swaps_species_on_every_photo(ct, series, species, voters):
    fox, deer, _ = species
    cl = voters[0]
    assert _vote(cl, series, fox.id).status_code == 201
    assert _vote(cl, series, deer.id, replace=True).status_code == 201

    mine = _mine(ct, series, _uid(cl))
    assert len(mine) == 2                       # still one row per photo
    assert {i.species_id for i in mine} == {deer.id}


def test_without_replace_the_old_answer_is_kept(ct, series, species, voters):
    """Insert-only is the default: a double click must not rewrite a vote."""
    fox, deer, _ = species
    cl = voters[0]
    _vote(cl, series, fox.id)
    _vote(cl, series, deer.id)

    assert {i.species_id for i in _mine(ct, series, _uid(cl))} == {fox.id}


def test_replace_does_not_touch_other_verifiers(ct, series, species, voters):
    fox, deer, _ = species
    _vote(voters[0], series, fox.id)
    _vote(voters[1], series, fox.id)
    _vote(voters[0], series, deer.id, replace=True)

    assert {i.species_id for i in _mine(ct, series, _uid(voters[1]))} == {fox.id}


def test_replace_keeps_identification_count_in_step(ct, series, species, voters):
    fox, deer, _ = species
    _vote(voters[0], series, fox.id)
    _vote(voters[1], series, fox.id)
    _vote(voters[0], series, deer.id, replace=True)
    ct.expire_all()

    assert [p.identification_count for p in series.photos] == [2, 2]


def test_replace_swaps_behaviors_and_comment(ct, series, species, voters):
    from app.camera_traps.models import BehaviorType
    fox, _, _ = species
    feeding = BehaviorType(name_ua='Годується', name_en='Feeding')
    resting = BehaviorType(name_ua='Відпочиває', name_en='Resting')
    ct.add_all([feeding, resting])
    ct.commit()

    cl = voters[0]
    _vote(cl, series, fox.id, behaviors=[feeding.id], comment='перше')
    _vote(cl, series, fox.id, behaviors=[resting.id], comment='друге', replace=True)

    mine = _mine(ct, series, _uid(cl))
    assert {i.comment for i in mine} == {'друге'}
    assert all([b.id for b in i.behaviors] == [resting.id] for i in mine)


def test_replace_without_prior_answer_just_inserts(ct, series, species, voters):
    fox, _, _ = species
    assert _vote(voters[0], series, fox.id, replace=True).status_code == 201
    assert len(_mine(ct, series, _uid(voters[0]))) == 2


def test_replace_never_clears_a_favourite(ct, series, species, voters):
    fox, deer, _ = species
    cl = voters[0]
    _vote(cl, series, fox.id, is_favorite=True, current_photo_index=0)
    _vote(cl, series, deer.id, is_favorite=False, current_photo_index=0, replace=True)
    ct.expire_all()

    assert sorted(series.photos, key=lambda p: p.captured_at)[0].is_favorite is True


def test_replace_can_reopen_a_closed_series(ct, series, species, voters):
    """The corrected vote withdraws the majority, so the series must reopen."""
    fox, deer, boar = species
    _vote(voters[0], series, fox.id)
    _vote(voters[1], series, deer.id)
    _vote(voters[2], series, fox.id)
    ct.expire_all()
    assert series.status == 'completed'

    _vote(voters[2], series, boar.id, replace=True)   # fox 1, deer 1, boar 1
    ct.expire_all()
    assert series.status == 'pending'
    assert {p.status for p in series.photos} == {'pending'}


def test_replace_can_close_a_series_with_a_new_winner(ct, series, species, voters):
    fox, deer, _ = species
    _vote(voters[0], series, fox.id)
    _vote(voters[1], series, deer.id)
    _vote(voters[2], series, fox.id)                  # fox wins 2/3

    _vote(voters[2], series, deer.id, replace=True)   # deer wins 2/3
    ct.expire_all()
    assert series.status == 'completed'
    winners = {}
    for i in _rows(ct, series):
        winners[i.species_id] = winners.get(i.species_id, 0) + 1
    assert winners == {fox.id: 2, deer.id: 4}


def test_step_back_payload_carries_my_answer(ct, series, species, voters):
    from app.camera_traps.models import BehaviorType
    fox, _, _ = species
    walking = BehaviorType(name_ua='Іде', name_en='Walking')
    ct.add(walking)
    ct.commit()

    cl = voters[0]
    _vote(cl, series, fox.id, behaviors=[walking.id], comment='нотатка')
    _fresh_user()
    resp = cl.get(NEXT_URL, query_string={'start_obs_id': series.id})

    assert resp.status_code == 200
    mine = resp.get_json()['my_identification']
    assert mine == {'species_id': fox.id, 'quantity': 1, 'comment': 'нотатка',
                    'behavior_ids': [walking.id]}


def test_step_back_payload_has_no_answer_for_other_verifiers(ct, series, species, voters):
    """Someone else's vote must never be offered as 'mine'."""
    fox, _, _ = species
    _vote(voters[0], series, fox.id)
    _fresh_user()
    resp = voters[1].get(NEXT_URL, query_string={'start_obs_id': series.id})

    assert resp.status_code == 200
    assert 'my_identification' not in resp.get_json()


def test_reopen_observation_is_a_noop_for_pending_series(ct, series):
    from app.camera_traps.utils import reopen_observation
    reopen_observation(series.id, db_session=ct)
    assert series.status == 'pending'


# --- the page itself ---------------------------------------------------------

def test_page_has_a_narrow_back_button_before_submit():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / 'app/camera_traps/templates'
            / 'identification.html').read_text(encoding='utf-8')
    back = html.index('id="back-btn"')
    assert back < html.index('type="submit"')
    assert 'MAX_BACK_STEPS = 3' in html
    assert 'replace: editingSubmitted' in html
