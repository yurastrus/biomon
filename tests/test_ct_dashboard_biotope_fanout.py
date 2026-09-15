"""
Dashboard counters must not multiply when a location carries several biotopes.

Found on 2026-09-15 while batching the dashboard aggregates. The filter was
`JOIN location_biotopes` plus `Biotope.id.in_(...)`, which produces one row per
matching biotope. Any counter that was not already a COUNT(DISTINCT) then
counted the same photo once per biotope: on prod, a three-biotope filter made
the photo counter read 1,504,083 instead of 808,460 — 86 % too high. 314 of 914
prod locations carry more than one biotope, up to six.

A membership test against location_biotopes cannot fan out, so the fix holds for
every counter on the page and for any number of selected biotopes.

Run:
    venv/Scripts/python -m pytest tests/test_ct_dashboard_biotope_fanout.py -v
"""
import re
from datetime import datetime
from unittest.mock import patch

import pytest

DASHBOARD = '/uk/camera-traps/dashboard'
WINDOW = '?start_date=2025-01-01&end_date=2025-12-31'


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.fixture
def photo_in_three_biotopes(ct_route_session, make_ct_location, make_ct_photo,
                            make_ct_observation):
    """One photo, at one location that belongs to three biotopes."""
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
    make_ct_photo(observation=observation, captured_at=datetime(2025, 6, 1, 12, 0))
    return location, biotopes


def _stat_values(html):
    return [int(v) for v in re.findall(r'class="stat-value">(\d+)<', html)]


def _biotope_query(biotopes):
    """The page's multi-select submits one `biotopes` parameter per option, and
    the route reads it with getlist(). A comma-joined value is silently ignored,
    so a test that used one would pass without ever applying the filter."""
    return ''.join(f'&biotopes={b.id}' for b in biotopes)


def test_one_photo_stays_one_across_three_selected_biotopes(
        auth_client, db_session, photo_in_three_biotopes):
    _, biotopes = photo_in_three_biotopes
    cl = auth_client(role='admin')
    resp = cl.get(f'{DASHBOARD}{WINDOW}{_biotope_query(biotopes)}')

    assert resp.status_code == 200
    values = _stat_values(resp.get_data(as_text=True))
    assert values, 'no stat cards rendered — the assertion below would be vacuous'
    assert values[0] == 1, (
        f'one photo counted once per selected biotope: got {values[0]}')


def test_selecting_one_biotope_gives_the_same_count_as_selecting_three(
        auth_client, db_session, photo_in_three_biotopes):
    """The location matches either way, so the numbers must not move."""
    _, biotopes = photo_in_three_biotopes
    cl = auth_client(role='admin')

    one = _stat_values(cl.get(
        f'{DASHBOARD}{WINDOW}{_biotope_query(biotopes[:1])}').get_data(as_text=True))
    three = _stat_values(cl.get(
        f'{DASHBOARD}{WINDOW}{_biotope_query(biotopes)}').get_data(as_text=True))

    assert one == three, f'{one} != {three}'


def test_a_biotope_nothing_matches_counts_nothing(
        auth_client, db_session, photo_in_three_biotopes):
    """Guards against a filter that silently stopped narrowing."""
    _, biotopes = photo_in_three_biotopes
    unmatched = max(b.id for b in biotopes) + 1000

    cl = auth_client(role='admin')
    values = _stat_values(cl.get(
        f'{DASHBOARD}{WINDOW}&biotopes={unmatched}').get_data(as_text=True))

    assert values, 'no stat cards rendered'
    assert set(values) == {0}, f'expected every counter at zero, got {values}'
