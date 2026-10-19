"""Photo viewer must not reveal who identified a photo.

Verification is blind: each identification is shown as "Верифікатор N", numbered
by first identification across the whole observation, so the same person keeps
the same number on every photo of the series. Usernames never reach the page.
"""
from datetime import datetime

import pytest


@pytest.fixture
def viewer(app, db_session, ct_session, auth_client, make_user, monkeypatch,
           make_ct_species, make_ct_observation, make_ct_photo):
    from app.camera_traps import routes
    from app.camera_traps.models import Identification

    monkeypatch.setattr(routes, 'get_ct_session', lambda: ct_session)
    monkeypatch.setattr(routes, 'close_ct_session', lambda: None)

    species = make_ct_species()
    obs = make_ct_observation()
    photo_a = make_ct_photo(observation=obs, captured_at=datetime(2025, 1, 1, 12, 0))
    photo_b = make_ct_photo(observation=obs, captured_at=datetime(2025, 1, 1, 12, 1))

    alice = make_user(username='secret_alice', roles=('user',))
    bob = make_user(username='secret_bob', roles=('user',))

    def ident(photo, user):
        ct_session.add(Identification(photo_id=photo.id, user_id=user.id,
                                      species_id=species.id, quantity=1))
        ct_session.commit()

    # Bob identifies first, Alice second; Alice also skips nothing on photo B.
    ident(photo_a, bob)
    ident(photo_a, alice)
    ident(photo_b, alice)

    client = auth_client(role='user', username='secret_viewer')

    def get(index):
        return client.get(
            f'/uk/camera-traps/observation/{obs.id}/photo/{index}')

    return get


def test_usernames_are_not_rendered(viewer):
    for index in (0, 1):
        resp = viewer(index)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'secret_alice' not in body
        assert 'secret_bob' not in body


def test_verifiers_are_numbered_by_first_identification(viewer):
    body = viewer(0).get_data(as_text=True)
    assert 'Верифікатор 1' in body
    assert 'Верифікатор 2' in body


def test_numbering_is_stable_across_photos_of_the_series(viewer):
    """Alice is Verifier 2 on photo A, so she must be Verifier 2 on photo B too."""
    body = viewer(1).get_data(as_text=True)
    assert 'Верифікатор 2' in body
    assert 'Верифікатор 1' not in body
