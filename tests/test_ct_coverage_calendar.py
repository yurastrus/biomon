"""
#38: CT календар покриття фотопасткою.

Покриття = «камера працювала»: deployment-інтервали ∪ дні з фото з
заповненням прогалин ≤ COVERAGE_MAX_GAP_DAYS. Інтенсивність — к-сть фото/день.

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_coverage_calendar.py -v
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from app.camera_traps.utils import fill_day_gaps, build_ct_coverage_calendar


# ── fill_day_gaps ────────────────────────────────────────────────────────────

def test_fill_gaps_within_threshold():
    days = {date(2025, 6, 1), date(2025, 6, 4)}  # прогалина 2 дні (2,3)
    out = fill_day_gaps(days, max_gap_days=10)
    assert date(2025, 6, 2) in out and date(2025, 6, 3) in out
    assert len(out) == 4


def test_no_fill_when_gap_exceeds_threshold():
    days = {date(2025, 6, 1), date(2025, 6, 20)}  # прогалина 18 днів > 10
    out = fill_day_gaps(days, max_gap_days=10)
    assert out == days  # не заповнено


def test_fill_gaps_zero_threshold_noop():
    days = {date(2025, 6, 1), date(2025, 6, 3)}
    assert fill_day_gaps(days, 0) == days


def test_fill_gaps_empty():
    assert fill_day_gaps(set(), 10) == set()


# ── build_ct_coverage_calendar ───────────────────────────────────────────────

def _find(cov, target):
    for mo in cov['months']:
        for wk in mo['weeks']:
            for c in wk:
                if c and c['date'] == target:
                    return c
    return None


def test_levels_covered_with_and_without_photos():
    covered = {date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)}
    photos = {date(2025, 6, 1): 5}  # лише 1-го є фото
    cov = build_ct_coverage_calendar(covered, photos, good_photos=1)
    assert _find(cov, date(2025, 6, 1))['level'] == 'good'      # камера + фото
    assert _find(cov, date(2025, 6, 2))['level'] == 'partial'   # камера, 0 фото
    assert _find(cov, date(2025, 6, 10))['level'] == 'missing'  # не працювала
    assert cov['total_photos'] == 5
    assert cov['active_camera_days'] == 3
    assert cov['days_with_photos'] == 1


def test_aggregated_mode_sums_across_years():
    """#39: aggregated зводить (місяць,день) за всі роки."""
    covered = {date(2024, 5, 1), date(2025, 5, 1)}
    photos = {date(2024, 5, 1): 3, date(2025, 5, 1): 2}
    cov = build_ct_coverage_calendar(covered, photos, mode='aggregated')
    assert cov['mode'] == 'aggregated'
    assert len(cov['months']) == 12
    cell = _find(cov, date(2000, 5, 1))
    assert cell['photos'] == 5
    assert cell['years'] == 2
    assert cell['covered'] is True
    assert cell['level'] == 'good'


def test_empty_calendar():
    cov = build_ct_coverage_calendar(set(), {})
    assert cov['months'] == []
    assert cov['day_range'] is None


def test_range_spans_deployment_and_photos():
    covered = {date(2025, 1, 5)}
    photos = {date(2025, 3, 20): 2}
    cov = build_ct_coverage_calendar(covered, photos)
    assert cov['day_range'] == (date(2025, 1, 5), date(2025, 3, 20))
    assert [m['label'] for m in cov['months']] == ['2025-01', '2025-02', '2025-03']


# ── Route (інтеграційний, SQLite ct_session) ─────────────────────────────────

@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def test_route_renders_with_deployment_and_photos(
        auth_client, db_session, ct_route_session,
        make_ct_location, make_ct_deployment, make_ct_observation, make_ct_photo):
    loc = make_ct_location(name='Ліс-1')
    make_ct_deployment(location=loc, start_date=date(2025, 6, 1), end_date=date(2025, 6, 10))
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs, captured_at=datetime(2025, 6, 3, 12, 0))

    cl = auth_client(role='admin')
    resp = cl.get(f'/uk/camera-traps/location/{loc.id}/coverage')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Ліс-1' in html
    assert 'coverage-good' in html      # деплоймент + фото
    assert 'coverage-partial' in html   # деплоймент-дні без фото


def test_route_requires_manager(auth_client, db_session, ct_route_session,
                                make_ct_location):
    loc = make_ct_location()
    cl = auth_client(role='viewer')
    resp = cl.get(f'/uk/camera-traps/location/{loc.id}/coverage')
    assert resp.status_code in (302, 403)
