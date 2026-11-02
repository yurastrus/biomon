"""
/identify prefetches the next series while the current one is on screen.

Reported 02.11.2026: after submitting an answer the next series took about a
second to appear. The page waited for the save, then for the next-series query
(~0.5 s of SQL on production), then for the thumbnails, strictly in turn.

Now the page asks for the next series in advance with `exclude_obs_id` set to
the series on screen, and the query checks one photo per series instead of
joining every photo to every identification.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identify_prefetch.py -v
"""
from pathlib import Path
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/next-observation-for-identification'
TEMPLATE = (Path(__file__).resolve().parents[1]
            / 'app' / 'camera_traps' / 'templates' / 'identification.html')


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _series(make_ct_observation, make_ct_photo, n_photos=2):
    obs = make_ct_observation(photo_count=n_photos)
    for _ in range(n_photos):
        make_ct_photo(observation=obs)
    return obs


def _vote_all_photos(ct_session, obs, user_id):
    from app.camera_traps.models import Identification, Photo
    for photo in ct_session.query(Photo).filter_by(observation_id=obs.id):
        ct_session.add(Identification(photo_id=photo.id, user_id=user_id, species_id=None))
    ct_session.commit()


def test_excluded_series_is_never_returned(auth_client, db_session, ct_route_session,
                                           make_ct_observation, make_ct_photo):
    a = _series(make_ct_observation, make_ct_photo)
    b = _series(make_ct_observation, make_ct_photo)
    cl = auth_client(role='admin')
    for _ in range(20):
        resp = cl.get(URL, query_string={'exclude_obs_id': a.id})
        assert resp.status_code == 200
        assert resp.get_json()['observation_id'] == b.id


def test_excluding_the_only_series_gives_404(auth_client, db_session, ct_route_session,
                                             make_ct_observation, make_ct_photo):
    a = _series(make_ct_observation, make_ct_photo)
    cl = auth_client(role='admin')
    assert cl.get(URL, query_string={'exclude_obs_id': a.id}).status_code == 404
    # Without the exclusion the same series is still there.
    assert cl.get(URL).get_json()['observation_id'] == a.id


def test_exclusion_works_with_every_sort(auth_client, db_session, ct_route_session,
                                         make_ct_observation, make_ct_photo):
    a = _series(make_ct_observation, make_ct_photo)
    b = _series(make_ct_observation, make_ct_photo)
    cl = auth_client(role='admin')
    for sort_by in ('priority_random', 'random', 'date_desc', 'date_asc',
                    'photo_count_desc', 'photo_count_asc'):
        resp = cl.get(URL, query_string={'exclude_obs_id': b.id, 'sort_by': sort_by})
        assert resp.get_json()['observation_id'] == a.id, sort_by


def test_series_i_answered_stays_hidden(auth_client, db_session, ct_route_session,
                                        make_ct_observation, make_ct_photo):
    """The one-photo check must still hide a series the caller has answered."""
    from app.models import User
    a = _series(make_ct_observation, make_ct_photo, n_photos=3)
    b = _series(make_ct_observation, make_ct_photo, n_photos=3)
    cl = auth_client(role='admin')
    user = User.query.filter_by(username='test_admin').one()
    _vote_all_photos(ct_route_session, a, user.id)
    for _ in range(10):
        assert cl.get(URL).get_json()['observation_id'] == b.id


def test_voted_tier_still_comes_first(auth_client, db_session, ct_route_session,
                                      make_ct_observation, make_ct_photo):
    fresh = _series(make_ct_observation, make_ct_photo)
    voted = _series(make_ct_observation, make_ct_photo)
    _vote_all_photos(ct_route_session, voted, user_id=999)
    cl = auth_client(role='admin')
    for _ in range(20):
        assert cl.get(URL).get_json()['observation_id'] == voted.id
    assert fresh.id != voted.id


def test_page_prefetches_with_exclusion():
    html = TEMPLATE.read_text(encoding='utf-8')
    assert 'function prefetchNextObservation' in html
    assert "params.append('exclude_obs_id', currentObservation.observation_id)" in html
    # The prefetched series is only used for the same filters and the same series on screen.
    assert 'entry.key !== key' in html
    assert 'entry.excludeId !== currentObservation.observation_id' in html
