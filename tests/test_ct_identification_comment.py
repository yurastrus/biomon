"""
#47: free-text comment in the identification form (<=200 chars).

Tests submit_identification:
  - comment is saved to Identification.comment;
  - >200 chars is truncated to 200 (backend guard);
  - empty / whitespace-only / missing -> None.

Consensus logic is mocked (we only test comment storage here).
"""
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/submit-identification'


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'), \
         patch('app.camera_traps.routes.check_consensus_for_observation'):
        yield ct_session


def _submit(cl, obs_id, species_id, comment=...):
    payload = {'observation_id': obs_id, 'species_id': species_id, 'quantity': 1}
    if comment is not ...:                     # ... = key omitted entirely
        payload['comment'] = comment
    return cl.post(URL, json=payload)


def _saved(ct, photo_id):
    from app.camera_traps.models import Identification
    return ct.query(Identification).filter_by(photo_id=photo_id).first()


def test_form_has_comment_field():
    from app.camera_traps.forms import IdentificationForm
    assert hasattr(IdentificationForm, 'comment')


def test_comment_saved(auth_client, db_session, ct_route_session,
                       make_ct_photo, make_ct_species):
    sp = make_ct_species()
    photo = make_ct_photo()
    cl = auth_client(role='ct_verifier')
    resp = _submit(cl, photo.observation_id, sp.id, comment='розмите фото, не певен')
    assert resp.status_code == 201, resp.get_data(as_text=True)
    ident = _saved(ct_route_session, photo.id)
    assert ident is not None
    assert ident.comment == 'розмите фото, не певен'


def test_comment_truncated_to_200(auth_client, db_session, ct_route_session,
                                  make_ct_photo, make_ct_species):
    sp = make_ct_species()
    photo = make_ct_photo()
    cl = auth_client(role='ct_verifier')
    resp = _submit(cl, photo.observation_id, sp.id, comment='я' * 350)
    assert resp.status_code == 201
    ident = _saved(ct_route_session, photo.id)
    assert len(ident.comment) == 200


def test_blank_comment_is_none(auth_client, db_session, ct_route_session,
                               make_ct_photo, make_ct_species):
    sp = make_ct_species()
    photo = make_ct_photo()
    cl = auth_client(role='ct_verifier')
    resp = _submit(cl, photo.observation_id, sp.id, comment='   ')
    assert resp.status_code == 201
    assert _saved(ct_route_session, photo.id).comment is None


def test_missing_comment_is_none(auth_client, db_session, ct_route_session,
                                 make_ct_photo, make_ct_species):
    sp = make_ct_species()
    photo = make_ct_photo()
    cl = auth_client(role='ct_verifier')
    resp = _submit(cl, photo.observation_id, sp.id)   # comment key missing
    assert resp.status_code == 201
    assert _saved(ct_route_session, photo.id).comment is None
