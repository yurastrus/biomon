"""
Dashboard `locations` parameter: three states in one string.

Noticed 2026-09-15 from a DevTools screenshot — the dashboard URL spelled out
every location id (~4 kB of query string for 914 locations) just to express
"all data". That happened because the parameter had only two states: a list, or
an empty string meaning "no filter". So the page could not say "all" without
listing everything, and it could not say "nothing" at all — deselecting every
marker produced the same empty string and therefore showed ALL data.

`parse_location_ids` + the 'none' sentinel give the parameter its third state.

Run:
    venv/Scripts/python -m pytest tests/test_ct_dashboard_location_param.py -v
"""
import re
from unittest.mock import patch

import pytest

from app.camera_traps.routes import NO_LOCATIONS_SENTINEL_ID, parse_location_ids

DASHBOARD = '/uk/camera-traps/dashboard'
TEMPLATE = 'app/camera_traps/templates/dashboard.html'


# ── parse_location_ids ──────────────────────────────────────────────────────

@pytest.mark.parametrize('raw', ['', '   ', None])
def test_empty_means_no_filter(raw):
    assert parse_location_ids(raw) == []


def test_none_means_no_locations():
    assert parse_location_ids('none') == [NO_LOCATIONS_SENTINEL_ID]
    assert parse_location_ids(' NONE ') == [NO_LOCATIONS_SENTINEL_ID]


def test_sentinel_cannot_collide_with_a_real_location():
    """Location ids are positive, so the sentinel narrows any IN() to nothing."""
    assert NO_LOCATIONS_SENTINEL_ID < 0


def test_a_list_is_parsed_and_junk_dropped():
    assert parse_location_ids('12,7,9') == [12, 7, 9]
    assert parse_location_ids('12,,abc,-3,9') == [12, 9]


def test_none_and_an_empty_list_are_no_longer_the_same_value():
    """The whole point: these two used to be indistinguishable."""
    assert parse_location_ids('none') != parse_location_ids('')


# ── the page ────────────────────────────────────────────────────────────────

@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


@pytest.fixture
def one_photo(ct_route_session, make_ct_photo):
    from datetime import datetime
    return make_ct_photo(captured_at=datetime(2025, 6, 1, 12, 0))


def _stat_values(html):
    """The six numbers rendered in the stat cards, in template order."""
    return [int(v) for v in re.findall(r'class="stat-value">(\d+)<', html)]


def test_dashboard_without_the_parameter_counts_the_photo(auth_client, db_session,
                                                          one_photo):
    cl = auth_client(role='admin')
    resp = cl.get(f'{DASHBOARD}?start_date=2025-01-01&end_date=2025-12-31')
    assert resp.status_code == 200
    values = _stat_values(resp.get_data(as_text=True))
    assert values, 'no stat cards rendered - the assertion below would be vacuous'
    assert values[0] == 1, 'the photo must be counted when no filter is given'


def test_dashboard_with_none_counts_nothing(auth_client, db_session, one_photo):
    """Deselecting every marker must mean no data, not all data."""
    cl = auth_client(role='admin')
    resp = cl.get(f'{DASHBOARD}?start_date=2025-01-01&end_date=2025-12-31'
                  f'&locations=none')
    assert resp.status_code == 200
    values = _stat_values(resp.get_data(as_text=True))
    assert values, 'no stat cards rendered - the assertion below would be vacuous'
    assert set(values) == {0}, f'expected every metric at zero, got {values}'


def test_top_species_api_honours_none(auth_client, db_session, one_photo):
    cl = auth_client(role='admin')
    resp = cl.get('/uk/camera-traps/api/stats/top-species'
                  '?start_date=2025-01-01&end_date=2025-12-31&locations=none')
    assert resp.status_code == 200
    assert resp.get_json()['labels'] == []


# ── the template side of the contract ───────────────────────────────────────

def test_template_sends_an_empty_value_when_everything_is_selected():
    """A shape assertion: the ~4 kB URL is invisible to any server-side test."""
    src = open(TEMPLATE, encoding='utf-8').read()
    assert 'function serialiseSelection()' in src
    assert "if (coversEverything) return '';" in src
    assert "return allVisibleLocationIds.size > 0 ? 'none' : '';" in src
    # The old unconditional dump of every id must be gone.
    assert "locationsInput.val(selectedArray.join(','))" not in src
