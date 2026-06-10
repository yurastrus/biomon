"""Daily-activity species dropdown: only species with enough verified
registrations are listed.

`get_cached_species_for_filter()` feeds the species dropdown on
`/<lang>/camera-traps/analysis/daily-activity`. It must list only species
with at least MIN_DETECTIONS_FOR_ACTIVITY verified registrations — i.e. the
consensus-winning species of completed/archived observations — so the dropdown
never offers species the activity chart cannot plot.

Run:
    venv/Scripts/python -m pytest tests/test_ct_daily_activity_species_filter.py -v
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def routes_with_ct(app, ct_session):
    """Point camera_traps.routes at the in-memory CT session and reset the
    module-level species-list cache so each test queries fresh."""
    from app.camera_traps import routes as ct_routes
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        ct_routes._species_list_cache['data'] = None
        ct_routes._species_list_cache['timestamp'] = None
        with app.app_context():
            yield ct_routes


def _add_registrations(ct_session, make_ct_observation, make_ct_photo,
                       species, n, status='completed'):
    """Create `n` observations, each with one photo + one identification vote
    for `species`, so the consensus winner of each is that species."""
    from app.camera_traps.models import Identification
    for _ in range(n):
        obs = make_ct_observation(status=status)
        photo = make_ct_photo(observation=obs)
        ct_session.add(Identification(
            photo_id=photo.id, user_id=1, species_id=species.id))
    ct_session.commit()


def test_threshold_is_thirty(routes_with_ct):
    """The documented constant value must not drift silently."""
    assert routes_with_ct.MIN_DETECTIONS_FOR_ACTIVITY == 30


def test_species_filtered_by_min_registrations(
        routes_with_ct, ct_session, make_ct_species,
        make_ct_observation, make_ct_photo, monkeypatch):
    """At threshold 3: a species with 3 registrations is listed (boundary is
    inclusive); one with 2 is hidden."""
    monkeypatch.setattr(routes_with_ct, 'MIN_DETECTIONS_FOR_ACTIVITY', 3)

    sp_in = make_ct_species(scientific_name='Canis lupus', common_name_ua='Вовк')
    sp_out = make_ct_species(scientific_name='Lutra lutra', common_name_ua='Видра')

    _add_registrations(ct_session, make_ct_observation, make_ct_photo, sp_in, 3)
    _add_registrations(ct_session, make_ct_observation, make_ct_photo, sp_out, 2)

    listed_ids = {s['id'] for s in routes_with_ct.get_cached_species_for_filter()}

    assert sp_in.id in listed_ids
    assert sp_out.id not in listed_ids


def test_unverified_observations_do_not_count(
        routes_with_ct, ct_session, make_ct_species,
        make_ct_observation, make_ct_photo, monkeypatch):
    """Observations in a non-completed/archived status must not count toward
    the threshold, so a species seen only in pending series stays hidden."""
    monkeypatch.setattr(routes_with_ct, 'MIN_DETECTIONS_FOR_ACTIVITY', 3)

    sp_pending = make_ct_species(scientific_name='Felis silvestris',
                                 common_name_ua='Кіт лісовий')
    _add_registrations(ct_session, make_ct_observation, make_ct_photo,
                       sp_pending, 5, status='pending')

    listed_ids = {s['id'] for s in routes_with_ct.get_cached_species_for_filter()}

    assert sp_pending.id not in listed_ids


def test_archived_observations_count(
        routes_with_ct, ct_session, make_ct_species,
        make_ct_observation, make_ct_photo, monkeypatch):
    """Archived observations are verified registrations and must count."""
    monkeypatch.setattr(routes_with_ct, 'MIN_DETECTIONS_FOR_ACTIVITY', 3)

    sp_arch = make_ct_species(scientific_name='Meles meles',
                              common_name_ua='Борсук')
    _add_registrations(ct_session, make_ct_observation, make_ct_photo,
                       sp_arch, 3, status='archived')

    listed_ids = {s['id'] for s in routes_with_ct.get_cached_species_for_filter()}

    assert sp_arch.id in listed_ids
