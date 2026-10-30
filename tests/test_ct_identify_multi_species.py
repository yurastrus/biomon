"""Several species in one series on /identify (2026-10).

A verifier may name several species for one series; each becomes its own row
per photo, with its own count and tags. Consensus counts PEOPLE, so a series
where everyone named the same two species closes, and both species are
accepted. Consensus runs for real against the in-memory SQLite session, as in
test_ct_identify_step_back.py.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/submit-identification'
NEXT_URL = '/uk/camera-traps/api/next-observation-for-identification'


@pytest.fixture(autouse=True)
def two_verifiers_close_a_series(app, monkeypatch):
    """Pin the production threshold (config.py: 2). Another test file replaces
    CAMERA_TRAP_CONFIG wholesale and never restores it."""
    monkeypatch.setitem(app.config, 'CAMERA_TRAP_CONFIG',
                        dict(app.config.get('CAMERA_TRAP_CONFIG') or {}, MIN_IDENTIFICATIONS=2))


@pytest.fixture
def ct(ct_session):
    from app.camera_traps import models as m
    m.identification_behaviors.create(bind=ct_session.get_bind(), checkfirst=True)
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.fixture
def series(ct, make_ct_location, make_ct_observation, make_ct_photo):
    loc = make_ct_location(name='Публічна', visibility_level=0)
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs, captured_at=datetime(2025, 1, 1, 12, 0))
    make_ct_photo(observation=obs, captured_at=datetime(2025, 1, 1, 12, 1))
    return obs


@pytest.fixture
def species(ct, make_ct_species):
    from app.camera_traps.models import Species
    animals = (make_ct_species(scientific_name='Vulpes vulpes'),
               make_ct_species(scientific_name='Meles meles',
                               common_name_ua='Борсук', common_name_en='Badger'),
               make_ct_species(scientific_name='Sus scrofa',
                               common_name_ua='Кабан', common_name_en='Boar'))
    # Created last: SQLite would otherwise number the next species 0 (max + 1).
    empty = Species(id=-1, scientific_name='empty', category='other',
                    common_name_ua='Пусто', common_name_en='Empty')
    ct.add(empty)
    ct.commit()
    return animals + (empty,)


@pytest.fixture
def behaviors(ct):
    from app.camera_traps.models import BehaviorType
    rows = [BehaviorType(name_ua='Харчування', name_en='Feeding'),
            BehaviorType(name_ua='Рух', name_en='Moving')]
    ct.add_all(rows)
    ct.commit()
    return rows


@pytest.fixture
def voters(auth_client):
    return [auth_client(role='ct_verifier', username=f'verifier_{i}') for i in range(3)]


def _fresh_user():
    from flask import g
    g.pop('_login_user', None)


def _post(cl, obs, entries, **extra):
    _fresh_user()
    payload = {'observation_id': obs.id, 'entries': entries}
    payload.update(extra)
    return cl.post(URL, json=payload)


def _e(sp, q=1, behaviors=()):
    return {'species_id': sp.id, 'quantity': q, 'behaviors': list(behaviors)}


def _rows(ct, obs, user_id=None):
    from app.camera_traps.models import Identification, Photo
    q = (ct.query(Identification).join(Photo)
         .filter(Photo.observation_id == obs.id))
    if user_id is not None:
        q = q.filter(Identification.user_id == user_id)
    return q.order_by(Identification.id).all()


def _uid(cl):
    with cl.session_transaction() as sess:
        return int(sess['_user_id'])


def test_two_species_give_one_row_per_species_per_photo(ct, series, species, voters):
    fox, badger, _, _ = species
    cl = voters[0]
    assert _post(cl, series, [_e(fox, 2), _e(badger, 1)]).status_code == 201
    rows = _rows(ct, series, _uid(cl))
    assert len(rows) == 4
    assert sorted((r.photo_id, r.species_id, r.quantity) for r in rows) == sorted(
        (p.id, sp, q) for p in series.photos for sp, q in ((fox.id, 2), (badger.id, 1)))


def test_counter_counts_people_not_species(ct, series, species, voters):
    fox, badger, _, _ = species
    _post(voters[0], series, [_e(fox), _e(badger)])
    ct.expire_all()
    assert [p.identification_count for p in series.photos] == [1, 1]


def test_tags_stay_with_their_species(ct, series, species, behaviors, voters):
    fox, badger, _, _ = species
    feeding, moving = behaviors
    cl = voters[0]
    _post(cl, series, [_e(fox, behaviors=[feeding.id]), _e(badger, behaviors=[moving.id])])
    by_species = {}
    for r in _rows(ct, series, _uid(cl)):
        by_species.setdefault(r.species_id, set()).update(b.id for b in r.behaviors)
    assert by_species == {fox.id: {feeding.id}, badger.id: {moving.id}}


def test_old_one_species_payload_still_works(ct, series, species, voters):
    fox, *_ = species
    cl = voters[0]
    _fresh_user()
    resp = cl.post(URL, json={'observation_id': series.id, 'species_id': fox.id, 'quantity': 3})
    assert resp.status_code == 201
    assert {(r.species_id, r.quantity) for r in _rows(ct, series, _uid(cl))} == {(fox.id, 3)}


@pytest.mark.parametrize('entries_of', [
    lambda s: [],                                           # nothing
    lambda s: [_e(s[0]), _e(s[0])],                         # same species twice
    lambda s: [_e(s[3]), _e(s[0])],                         # "empty" with a species
    lambda s: [_e(s[0], q=0)],                              # zero animals
    lambda s: [_e(s[0])] * 11,                              # absurd list
])
def test_invalid_entries_are_refused_and_write_nothing(ct, series, species, voters, entries_of):
    resp = _post(voters[0], series, entries_of(species))
    assert resp.status_code == 400
    assert _rows(ct, series) == []


def test_second_submit_without_replace_adds_nothing(ct, series, species, voters):
    """A stale second tab must not add a species to an answer already given."""
    fox, badger, _, _ = species
    cl = voters[0]
    _post(cl, series, [_e(fox)])
    _post(cl, series, [_e(badger)])
    assert {r.species_id for r in _rows(ct, series, _uid(cl))} == {fox.id}


def test_replace_swaps_the_whole_set(ct, series, species, voters):
    fox, badger, boar, _ = species
    cl = voters[0]
    _post(cl, series, [_e(fox), _e(badger)])
    _post(cl, series, [_e(boar), _e(fox, 4)], replace=True)
    rows = _rows(ct, series, _uid(cl))
    assert {(r.species_id, r.quantity) for r in rows} == {(boar.id, 1), (fox.id, 4)}
    ct.expire_all()
    assert [p.identification_count for p in series.photos] == [1, 1]


def test_everyone_naming_the_same_two_species_closes_the_series(ct, series, species, voters):
    """Before the fix, 2 people x 2 species looked like 4 voters with 2 votes
    each: never a majority, the series stayed pending forever."""
    fox, badger, _, _ = species
    _post(voters[0], series, [_e(fox), _e(badger)])
    _post(voters[1], series, [_e(fox), _e(badger)])
    ct.expire_all()
    assert series.status == 'completed'


def test_one_person_with_two_species_is_one_vote(ct, series, species, voters, app):
    """MIN_IDENTIFICATIONS counts people: one verifier naming two species must
    not close a series on their own."""
    fox, badger, _, _ = species
    _post(voters[0], series, [_e(fox), _e(badger)])
    ct.expire_all()
    assert app.config['CAMERA_TRAP_CONFIG'].get('MIN_IDENTIFICATIONS', 3) >= 2
    assert series.status == 'pending'


def test_split_votes_still_do_not_close(ct, series, species, voters):
    fox, badger, boar, _ = species
    _post(voters[0], series, [_e(fox)])
    _post(voters[1], series, [_e(badger)])
    ct.expire_all()
    assert series.status == 'pending'


def test_step_back_returns_every_species(ct, series, species, behaviors, voters):
    fox, badger, _, _ = species
    feeding, _moving = behaviors
    cl = voters[0]
    _post(cl, series, [_e(fox, 2, [feeding.id]), _e(badger, 1)])
    _fresh_user()
    data = cl.get(NEXT_URL, query_string={'start_obs_id': series.id}).get_json()
    mine = data.get('my_identifications')
    assert mine is not None, data
    assert [(m['species_id'], m['quantity'], m['behavior_ids']) for m in mine] == [
        (fox.id, 2, [feeding.id]), (badger.id, 1, [])]
    assert data['my_identification'] == mine[0]


def test_review_mode_shows_every_species_of_a_verifier(ct, series, species, voters, auth_client):
    """Moderators see one line per verifier, with all of their species and
    counts; a single-species verifier looks exactly as before."""
    fox, badger, boar, _ = species
    _post(voters[0], series, [_e(fox, 2), _e(badger, 1)])
    _post(voters[1], series, [_e(boar, 3)])
    admin = auth_client(role='admin')
    _fresh_user()
    data = admin.get(NEXT_URL + '?review=true').get_json()
    assert data['observation_id'] == series.id
    by_name = {e['username']: e for e in data['existing_identifications']}
    multi, single = by_name['verifier_0'], by_name['verifier_1']
    assert multi['species_name'] == 'Лисиця ×2 + Борсук ×1'
    assert multi['quantity'] is None
    assert (single['species_name'], single['quantity']) == ('Кабан', 3)
    assert set(single) == {'username', 'species_name', 'quantity', 'comment', 'created_at'}


def test_second_species_at_half_waits_for_the_next_verifier(ct, series, species, voters):
    """Owner's rule: fox+badger and fox -> badger 1/2 is undecided, the
    series stays open; the third verifier settles it."""
    fox, badger, _, _ = species
    _post(voters[0], series, [_e(fox), _e(badger)])
    _post(voters[1], series, [_e(fox)])
    ct.expire_all()
    assert series.status == 'pending'
    _post(voters[2], series, [_e(fox)])
    ct.expire_all()
    assert series.status == 'completed'


def test_single_species_series_closes_as_before(ct, series, species, voters):
    fox, *_ = species
    _post(voters[0], series, [_e(fox)])
    _post(voters[1], series, [_e(fox)])
    ct.expire_all()
    assert series.status == 'completed'
