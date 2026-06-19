"""
Idea 10 / #37: location coverage calendar based on the SUM of recording durations.

build_coverage_calendar accepts {date: {'count', 'minutes'}}; a day is good if
the total recording hours (minutes/60) ≥ COVERAGE_GOOD_HOURS (6).

Run:
    venv/Scripts/python -m pytest tests/test_pam_coverage_calendar.py -v
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.pam.utils import build_coverage_calendar

URL = '/uk/pam/location/9/coverage'


def _d(count, minutes):
    return {'count': count, 'minutes': minutes}


# ── Pure function ─────────────────────────────────────────────────────────────

def test_empty_input():
    cov = build_coverage_calendar({})
    assert cov['months'] == []
    assert cov['total_recordings'] == 0
    assert cov['total_hours'] == 0.0
    assert cov['active_days'] == 0
    assert cov['day_range'] is None


def test_none_date_key_ignored():
    """recordings without a date (DATE(NULL)=None) are ignored (regression)."""
    cov = build_coverage_calendar({None: _d(50, 250), date(2025, 6, 2): _d(10, 50)})
    assert cov['active_days'] == 1
    assert cov['total_recordings'] == 10
    assert cov['day_range'] == (date(2025, 6, 2), date(2025, 6, 2))


def test_only_none_dates_is_empty():
    cov = build_coverage_calendar({None: _d(99, 495)})
    assert cov['months'] == []
    assert cov['active_days'] == 0


def test_single_day_totals():
    cov = build_coverage_calendar({date(2025, 6, 2): _d(100, 500)})
    assert cov['total_recordings'] == 100
    assert cov['total_hours'] == round(500 / 60.0, 1)  # 8.3 h
    assert cov['active_days'] == 1
    assert cov['day_range'] == (date(2025, 6, 2), date(2025, 6, 2))
    assert cov['months'][0]['label'] == '2025-06'


def _find_cell(cov, target):
    for month in cov['months']:
        for week in month['weeks']:
            for cell in week:
                if cell is not None and cell['date'] == target:
                    return cell
    return None


def test_coverage_levels():
    # 480 min = 8 h ≥6 → good; 180 min = 3 h → partial; no recordings → missing
    cov = build_coverage_calendar(
        {date(2025, 6, 2): _d(96, 480), date(2025, 6, 3): _d(36, 180)})
    assert _find_cell(cov, date(2025, 6, 2))['level'] == 'good'
    assert _find_cell(cov, date(2025, 6, 2))['hours'] == 8.0
    assert _find_cell(cov, date(2025, 6, 3))['level'] == 'partial'
    assert _find_cell(cov, date(2025, 6, 15))['level'] == 'missing'


def test_boundary_6_hours_is_good():
    """Exactly 360 min = 6 h = good (threshold is not strict)."""
    cov = build_coverage_calendar({date(2025, 6, 2): _d(72, 360)})
    assert _find_cell(cov, date(2025, 6, 2))['level'] == 'good'
    assert _find_cell(cov, date(2025, 6, 2))['hours'] == 6.0


def test_intensity_linear():
    """#43: intensity is linear (min→0, max→1); 0 h → None (gray)."""
    cov = build_coverage_calendar({
        date(2025, 6, 1): _d(0, 0),     # 0 h → missing/None
        date(2025, 6, 2): _d(10, 180),  # 3 h → min → 0.0
        date(2025, 6, 3): _d(10, 360),  # 6 h → max → 1.0
    })
    assert _find_cell(cov, date(2025, 6, 1))['intensity'] is None
    assert _find_cell(cov, date(2025, 6, 2))['intensity'] == 0.0
    assert _find_cell(cov, date(2025, 6, 3))['intensity'] == 1.0


def test_aggregated_mode_sums_across_years():
    """#39: aggregated mode collapses (month,day) across all years + counts years."""
    cov = build_coverage_calendar(
        {date(2024, 5, 1): _d(10, 300), date(2025, 5, 1): _d(8, 240)},
        mode='aggregated')
    assert cov['mode'] == 'aggregated'
    assert len(cov['months']) == 12          # nominal year
    cell = _find_cell(cov, date(2000, 5, 1))  # 2000 — nominal leap year
    assert cell['hours'] == 9.0              # (300+240)/60
    assert cell['years'] == 2
    assert cov['years'] == [2024, 2025]


def test_day_hours_not_capped():
    """Multiple receivers at a location → daily sum legitimately > 24 h (no cap)."""
    cov = build_coverage_calendar({date(2025, 6, 2): _d(473, 2365)})  # 39.4 h
    cell = _find_cell(cov, date(2025, 6, 2))
    assert cell['hours'] == 39.4     # no limit
    assert cell['level'] == 'good'


def test_padding_cells_are_none():
    cov = build_coverage_calendar({date(2025, 6, 2): _d(10, 50)})
    weeks = cov['months'][0]['weeks']
    assert any(any(c is None for c in wk) for wk in weeks)


def test_spans_multiple_months():
    cov = build_coverage_calendar({date(2025, 1, 5): _d(10, 50), date(2025, 3, 20): _d(20, 100)})
    labels = [m['label'] for m in cov['months']]
    assert labels == ['2025-01', '2025-02', '2025-03']


# ── Route ────────────────────────────────────────────────────────────────────

def _coverage_conn(loc_row, day_rows):
    conn = MagicMock()

    def _execute(q, params=None):
        s = str(q)
        res = MagicMock()
        if 'FROM locations' in s:
            res.fetchone.return_value = loc_row
        elif 'location_institutions' in s:
            res.fetchone.return_value = (1,)
        elif 'FROM recordings' in s:
            res.fetchall.return_value = day_rows
        else:
            res.fetchone.return_value = None
            res.fetchall.return_value = []
        return res

    conn.execute.side_effect = _execute
    return conn


def test_coverage_route_renders(auth_client):
    cl = auth_client(role='admin')
    loc = SimpleNamespace(location_id=9, location_name='Тестова локація',
                          location_name_en='Test Loc')
    days = [SimpleNamespace(day=date(2025, 6, 2), cnt=96, minutes=480),
            SimpleNamespace(day=date(2025, 6, 3), cnt=36, minutes=180)]
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_coverage_conn(loc, days)):
        resp = cl.get(URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Тестова локація' in html
    assert 'rgba(76,175,80' in html      # gradient fill (#43)
    assert '2025-06' in html


def test_coverage_route_location_not_found(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_coverage_conn(None, [])):
        resp = cl.get('/uk/pam/location/999/coverage')
    assert resp.status_code in (302, 303)


def test_coverage_route_requires_role(auth_client):
    cl = auth_client(role='viewer')
    resp = cl.get(URL)
    assert resp.status_code in (302, 403)
