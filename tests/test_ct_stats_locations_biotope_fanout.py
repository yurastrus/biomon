"""
Map marker counts must not multiply when a location carries several biotopes.

Sibling of tests/test_ct_dashboard_biotope_fanout.py — same defect, other
endpoint. /api/stats/locations feeds the dashboard map, and its photo_count was
built from `JOIN location_biotopes` plus a plain `count(Photo.id)`, so a
location matching N selected biotopes reported N times its photos. On prod, with
three biotopes selected, 194 of 575 markers were 2–3x too high and the map
totalled 1,504,083 photos against 808,460 in the database.

Note the parameter shape differs from the dashboard on purpose: this endpoint
reads `biotopes` as ONE comma-separated value (the page builds it with
`searchParams.set('biotopes', ids.join(','))`), while the dashboard form posts
one parameter per biotope. A test written for the wrong shape passes without
ever applying the filter.

Run:
    venv/Scripts/python -m pytest tests/test_ct_stats_locations_biotope_fanout.py -v
"""
from datetime import datetime
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/stats/locations'
WINDOW = '?start_date=2025-01-01&end_date=2025-12-31'


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.fixture
def two_photos_in_three_biotopes(ct_route_session, make_ct_location,
                                 make_ct_observation, make_ct_photo):
    """Two photos at one location that belongs to three biotopes."""
    from app.camera_traps.models import Biotope

    location = make_ct_location()
    biotopes = []
    for name in ('Ліс', 'Луки', 'Болото'):
        biotope = Biotope(name_ua=name, name_en=name)
        ct_route_session.add(biotope)
        biotopes.append(biotope)
    ct_route_session.commit()

    location.biotopes.extend(biotopes)
    ct_route_session.commit()

    observation = make_ct_observation(location=location,
                                      series_start_time=datetime(2025, 6, 1, 12, 0))
    for minute in (0, 1):
        make_ct_photo(observation=observation,
                      captured_at=datetime(2025, 6, 1, 12, minute))
    return location, biotopes


def _counts(resp):
    return {marker['id']: marker['photo_count'] for marker in resp.get_json()}


def test_marker_count_is_not_multiplied_by_biotopes(
        auth_client, db_session, two_photos_in_three_biotopes):
    location, biotopes = two_photos_in_three_biotopes
    selected = ','.join(str(b.id) for b in biotopes)

    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}{WINDOW}&biotopes={selected}')

    assert resp.status_code == 200
    counts = _counts(resp)
    assert counts, 'no markers returned — the assertion below would be vacuous'
    assert counts[location.id] == 2, (
        f'two photos counted once per selected biotope: got {counts[location.id]}')


def test_count_is_the_same_for_one_biotope_and_for_three(
        auth_client, db_session, two_photos_in_three_biotopes):
    """The location matches either way, so its marker must not change."""
    _, biotopes = two_photos_in_three_biotopes
    cl = auth_client(role='admin')

    one = _counts(cl.get(f'{URL}{WINDOW}&biotopes={biotopes[0].id}'))
    three = _counts(cl.get(
        f'{URL}{WINDOW}&biotopes={",".join(str(b.id) for b in biotopes)}'))

    assert one == three, f'{one} != {three}'


def test_unmatched_biotope_returns_no_markers(
        auth_client, db_session, two_photos_in_three_biotopes):
    """Guards against a filter that silently stopped narrowing."""
    _, biotopes = two_photos_in_three_biotopes
    unmatched = max(b.id for b in biotopes) + 1000

    cl = auth_client(role='admin')
    resp = cl.get(f'{URL}{WINDOW}&biotopes={unmatched}')

    assert resp.status_code == 200
    assert resp.get_json() == []
