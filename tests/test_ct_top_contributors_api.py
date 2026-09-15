"""
Top contributors moved off the dashboard request into /api/stats/top-contributors.

Why it moved: the query is the most expensive on the page (725 ms of ~1.9 s on
prod) and its cost is structural — every identification in the window joined to
every photo in it, growing linearly with the data. Nothing about it got faster;
the page simply stopped waiting for it. The dashboard now renders its cards in
~640 ms instead of ~990 ms and the panel fills in afterwards.

What must keep holding:
  1. the endpoint returns the same shape the template used to render
  2. it honours the page's filters — a panel that disagreed with the cards
     beside it would be worse than a slow one
  3. the page no longer runs the query, and the panel is wired up

Run:
    venv/Scripts/python -m pytest tests/test_ct_top_contributors_api.py -v
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/stats/top-contributors'
DASHBOARD = '/uk/camera-traps/dashboard'
WINDOW = '?start_date=2025-01-01&end_date=2025-12-31'


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.fixture
def identified_series(ct_route_session, make_ct_location, make_ct_observation,
                      make_ct_photo, make_ct_species, make_user):
    """One verifier, one series of two photos, both identified by them.

    Two photos of one series must count as ONE identified series, which is the
    whole reason the query de-duplicates (user, observation) pairs.
    """
    from app.camera_traps.models import Identification

    user = make_user(username='verifier_one')
    species = make_ct_species()
    location = make_ct_location()
    observation = make_ct_observation(location=location,
                                      series_start_time=datetime(2025, 6, 1, 12, 0))
    for minute in (0, 1):
        photo = make_ct_photo(observation=observation,
                              captured_at=datetime(2025, 6, 1, 12, minute))
        ct_route_session.add(Identification(photo_id=photo.id, user_id=user.id,
                                            species_id=species.id, quantity=1))
    ct_route_session.commit()
    return user, location


def test_returns_username_and_series_count(auth_client, db_session, identified_series):
    user, _ = identified_series
    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}{WINDOW}')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == [{'username': user.username, 'observation_count': 1}], (
        'two photos of one series must count as one identified series')


def test_window_outside_the_data_returns_empty(auth_client, db_session,
                                               identified_series):
    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}?start_date=2020-01-01&end_date=2020-12-31')

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_locations_none_narrows_to_nothing(auth_client, db_session,
                                           identified_series):
    """The page's third location state must reach this panel too."""
    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}{WINDOW}&locations=none')

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_unmatched_biotope_narrows_to_nothing(auth_client, db_session,
                                              identified_series):
    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}{WINDOW}&biotopes=99999')

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_a_bad_date_is_rejected_not_ignored(auth_client, db_session):
    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}?start_date=not-a-date&end_date=2025-12-31')

    assert resp.status_code == 400


# ── the page side ───────────────────────────────────────────────────────────

def test_dashboard_no_longer_renders_contributors_server_side(
        auth_client, db_session, identified_series):
    user, _ = identified_series
    cl = auth_client(role='admin')
    html = cl.get(f'{DASHBOARD}{WINDOW}').get_data(as_text=True)

    assert 'id="contributors-panel"' in html
    assert 'loadContributors();' in html
    # The name must arrive from the API, not from the template.
    assert user.username not in html
