"""
Idea 6 (#19): прапорець «на повторний розгляд» для серій CT.

flag/unflag endpoints, admin-список, доступ. Flag — організаційна позначка
(НЕ змінює status, НЕ виключає з аналітики).

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_flag_review.py -v
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def test_model_has_flag_columns():
    from app.camera_traps.models import Observation
    cols = Observation.__table__.columns.keys()
    assert 'flagged' in cols
    assert 'flag_note' in cols


def test_flag_sets_flagged_and_note(auth_client, db_session, ct_route_session,
                                    make_ct_observation):
    obs = make_ct_observation()
    cl = auth_client(role='ct_verifier')
    resp = cl.post(f'/uk/camera-traps/observation/{obs.id}/flag',
                   data={'note': 'розмита серія'})
    assert resp.status_code in (302, 303)
    ct_route_session.refresh(obs)
    assert obs.flagged is True
    assert obs.flag_note == 'розмита серія'


def test_flag_without_note_sets_null(auth_client, db_session, ct_route_session,
                                     make_ct_observation):
    obs = make_ct_observation()
    cl = auth_client(role='ct_verifier')
    cl.post(f'/uk/camera-traps/observation/{obs.id}/flag', data={})
    ct_route_session.refresh(obs)
    assert obs.flagged is True
    assert obs.flag_note is None


def test_unflag_clears(auth_client, db_session, ct_route_session,
                       make_ct_observation):
    obs = make_ct_observation()
    obs.flagged = True
    obs.flag_note = 'x'
    ct_route_session.commit()
    cl = auth_client(role='ct_verifier')
    cl.post(f'/uk/camera-traps/observation/{obs.id}/unflag', data={})
    ct_route_session.refresh(obs)
    assert obs.flagged is False
    assert obs.flag_note is None


def test_admin_flagged_list_shows_flagged(auth_client, db_session,
                                          ct_route_session, make_ct_observation):
    obs = make_ct_observation()
    obs.flagged = True
    obs.flag_note = 'до перегляду'
    ct_route_session.commit()
    cl = auth_client(role='admin')
    resp = cl.get('/uk/camera-traps/admin/flagged')
    assert resp.status_code == 200
    assert 'до перегляду' in resp.get_data(as_text=True)


def test_flag_requires_ct_verifier(auth_client, db_session, ct_route_session,
                                   make_ct_observation):
    obs = make_ct_observation()
    cl = auth_client(role='viewer')
    resp = cl.post(f'/uk/camera-traps/observation/{obs.id}/flag', data={})
    assert resp.status_code in (302, 403)
    ct_route_session.refresh(obs)
    assert obs.flagged is False


def test_flagged_list_requires_admin(auth_client, db_session, ct_route_session):
    cl = auth_client(role='ct_verifier')
    resp = cl.get('/uk/camera-traps/admin/flagged')
    assert resp.status_code in (302, 403)
