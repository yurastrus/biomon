"""
Smoke-тести моделей camera_traps: Species, Location, Observation, Photo, Identification.

Виконуються на ізольованому SQLite in-memory engine з фікстури `ct_session`.
ARRAY/JSONB-моделі (AIPrediction, LocationMergeLog) — поза цим baseline.
"""
import pytest
from datetime import datetime


def test_species_create(ct_session, make_ct_species):
    sp = make_ct_species(scientific_name='Canis lupus', category='mammal')
    assert sp.id is not None
    assert sp.is_active is True


def test_species_unique_constraint(ct_session, make_ct_species):
    make_ct_species(scientific_name='Canis lupus')
    with pytest.raises(Exception):
        make_ct_species(scientific_name='Canis lupus')


def test_location_create(ct_session, make_ct_location):
    loc = make_ct_location(name='Carpathians', latitude=48.5, longitude=24.5)
    assert loc.id is not None
    assert str(loc.latitude) == '48.50000'


def test_observation_create(ct_session, make_ct_observation):
    obs = make_ct_observation()
    assert obs.id is not None
    assert obs.status == 'pending'
    assert obs.location is not None


def test_photo_belongs_to_observation(ct_session, make_ct_photo):
    photo = make_ct_photo()
    assert photo.id is not None
    assert photo.observation is not None
    assert photo in photo.observation.photos


def test_photo_unique_system_filename(ct_session, make_ct_photo):
    p1 = make_ct_photo(system_filename='unique.jpg')
    with pytest.raises(Exception):
        make_ct_photo(system_filename='unique.jpg', observation=p1.observation)


def test_identification_links_photo_species_user(ct_session, make_ct_photo,
                                                  make_ct_species):
    from app.camera_traps.models import Identification
    photo = make_ct_photo()
    species = make_ct_species()
    ident = Identification(photo_id=photo.id, user_id=42,
                           species_id=species.id)
    ct_session.add(ident)
    ct_session.commit()
    assert ident.id is not None
    assert ident.photo == photo
    assert ident.species == species


def test_identification_unique_per_photo_user(ct_session, make_ct_photo,
                                              make_ct_species):
    from app.camera_traps.models import Identification
    photo = make_ct_photo()
    species = make_ct_species()
    ct_session.add(Identification(photo_id=photo.id, user_id=42,
                                  species_id=species.id))
    ct_session.commit()
    ct_session.add(Identification(photo_id=photo.id, user_id=42,
                                  species_id=species.id))
    with pytest.raises(Exception):
        ct_session.commit()
