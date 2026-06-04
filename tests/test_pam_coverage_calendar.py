"""
Idea 10 (#18): календар покриття локації записами.

Покриває:
  - build_coverage_calendar (чиста функція): порожньо, рівні, padding;
  - route /pam/location/<id>/coverage: рендер (200), доступ (pam_verifier+).

Запуск:
    venv/Scripts/python -m pytest tests/test_pam_coverage_calendar.py -v
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.pam.utils import build_coverage_calendar


# ── Чиста функція ────────────────────────────────────────────────────────────

def _d(count, hours):
    return {'count': count, 'hours': hours}


def test_empty_input():
    cov = build_coverage_calendar({})
    assert cov['months'] == []
    assert cov['total_recordings'] == 0
    assert cov['active_days'] == 0
    assert cov['day_range'] is None


def test_none_date_key_ignored():
    """recordings без дати (DATE(NULL)=None ключ) не ламають сортування —
    None-день ігнорується (regression: TypeError None < date)."""
    cov = build_coverage_calendar({None: _d(50, 5), date(2025, 6, 2): _d(10, 3)})
    assert cov['active_days'] == 1
    assert cov['total_recordings'] == 10        # None-день не рахується
    assert cov['day_range'] == (date(2025, 6, 2), date(2025, 6, 2))


def test_only_none_dates_is_empty():
    cov = build_coverage_calendar({None: _d(99, 9)})
    assert cov['months'] == []
    assert cov['active_days'] == 0


def test_single_day_totals():
    cov = build_coverage_calendar({date(2025, 6, 2): _d(100, 8)})
    assert cov['total_recordings'] == 100
    assert cov['active_days'] == 1
    assert cov['day_range'] == (date(2025, 6, 2), date(2025, 6, 2))
    assert len(cov['months']) == 1
    assert cov['months'][0]['label'] == '2025-06'


def _find_cell(cov, target):
    for month in cov['months']:
        for week in month['weeks']:
            for cell in week:
                if cell is not None and cell['date'] == target:
                    return cell
    return None


def test_coverage_levels():
    # 8 год ≥6 → good; 3 год → partial; день без записів → missing
    cov = build_coverage_calendar(
        {date(2025, 6, 2): _d(2000, 8), date(2025, 6, 3): _d(100, 3)})
    assert _find_cell(cov, date(2025, 6, 2))['level'] == 'good'
    assert _find_cell(cov, date(2025, 6, 3))['level'] == 'partial'
    assert _find_cell(cov, date(2025, 6, 15))['level'] == 'missing'


def test_boundary_6_hours_is_good():
    """Рівно 6 год охоплення = good (поріг не строгий)."""
    cov = build_coverage_calendar({date(2025, 6, 2): _d(50, 6)})
    assert _find_cell(cov, date(2025, 6, 2))['level'] == 'good'
    assert _find_cell(cov, date(2025, 6, 2))['hours'] == 6


def test_padding_cells_are_none():
    """Дні сусіднього місяця в тижневих рядках = None (порожні клітинки)."""
    cov = build_coverage_calendar({date(2025, 6, 2): _d(10, 2)})
    weeks = cov['months'][0]['weeks']
    assert any(any(c is None for c in wk) for wk in weeks)


def test_spans_multiple_months():
    cov = build_coverage_calendar({date(2025, 1, 5): _d(10, 2), date(2025, 3, 20): _d(20, 4)})
    labels = [m['label'] for m in cov['months']]
    assert labels == ['2025-01', '2025-02', '2025-03']  # включно з порожнім лютим


# ── Route ────────────────────────────────────────────────────────────────────

def _coverage_conn(loc_row, day_rows):
    conn = MagicMock()

    def _execute(q, params=None):
        s = str(q)
        res = MagicMock()
        if 'FROM locations' in s:
            res.fetchone.return_value = loc_row
        elif 'location_institutions' in s:
            res.fetchone.return_value = (1,)  # доступ дозволено
        elif 'FROM recordings' in s:
            res.fetchall.return_value = day_rows
        else:
            res.fetchone.return_value = None
            res.fetchall.return_value = []
        return res

    conn.execute.side_effect = _execute
    return conn


def test_coverage_route_renders(auth_client):
    cl = auth_client(role='admin')  # admin обходить перевірку установи
    loc = SimpleNamespace(location_id=9, location_name='Тестова локація',
                          location_name_en='Test Loc')
    days = [SimpleNamespace(day=date(2025, 6, 2), cnt=2000, hours=20),
            SimpleNamespace(day=date(2025, 6, 3), cnt=50, hours=3)]
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_coverage_conn(loc, days)):
        resp = cl.get('/uk/pam/location/9/coverage')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Тестова локація' in html
    assert 'coverage-good' in html       # день з 20 год охоплення
    assert 'coverage-partial' in html    # день з 3 год
    assert '2025-06' in html             # місячний блок


def test_coverage_route_location_not_found(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection',
               return_value=_coverage_conn(None, [])):
        resp = cl.get('/uk/pam/location/999/coverage')
    # редірект на manage-locations із flash
    assert resp.status_code in (302, 303)


def test_coverage_route_requires_role(auth_client):
    """viewer без pam_verifier → 302/403 (CT/PAM role_required)."""
    cl = auth_client(role='viewer')
    resp = cl.get('/uk/pam/location/9/coverage')
    assert resp.status_code in (302, 403)
