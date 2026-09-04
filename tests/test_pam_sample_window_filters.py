# SPDX-License-Identifier: AGPL-3.0-only
"""Season / year / confidence-band narrowing on the PAM sample-upload page.

The page draws a confidence-stratified sample of detections. These tests cover
the filters added on top of that draw:

* an upper confidence bound, so a run can target a band (0.3-0.6);
* a month window that is deliberately **year-agnostic** — "February to April"
  means every February-April on record, which a plain date range cannot say;
* an inclusive year range, written as timestamp bounds so the
  ``datetime_start`` index still applies;
* the cached earliest-recording-year that seeds the year selector.

Run:
    venv/Scripts/python -m pytest tests/test_pam_sample_window_filters.py -v
"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.pam import pam_segment_sampling as pss


# ── build_sampling_query: the filters are shape switches ─────────────────────

def test_no_filters_leaves_the_query_untouched():
    """The historical query must not grow clauses nobody asked for."""
    sql = str(pss.build_sampling_query(10))
    assert ':conf_max' not in sql
    assert 'EXTRACT(MONTH' not in sql
    assert 'make_date' not in sql


def test_conf_max_adds_an_upper_bound_on_the_model_column():
    sql = str(pss.build_sampling_query(10, conf_column='conf_perch_v2',
                                       with_conf_max=True))
    assert 'd.conf_perch_v2 <= :conf_max' in sql
    # the lower bound survives — a band needs both
    assert 'd.conf_perch_v2 >= :conf_thr' in sql


def test_month_range_within_one_year_uses_between():
    sql = str(pss.build_sampling_query(10, month_mode='range'))
    assert 'EXTRACT(MONTH FROM r.datetime_start) BETWEEN :month_start AND :month_end' in sql
    assert ' OR EXTRACT(MONTH' not in sql


def test_month_range_across_new_year_uses_or():
    """Nov-Feb is one season but two calendar spans, so BETWEEN would be empty."""
    sql = str(pss.build_sampling_query(10, month_mode='wrap'))
    assert 'EXTRACT(MONTH FROM r.datetime_start) >= :month_start' in sql
    assert 'EXTRACT(MONTH FROM r.datetime_start) <= :month_end' in sql
    assert 'BETWEEN :month_start' not in sql


def test_year_range_is_written_as_index_friendly_bounds():
    """EXTRACT(YEAR ...) would forbid the datetime_start index; bounds don't."""
    sql = str(pss.build_sampling_query(10, with_year_range=True))
    assert 'r.datetime_start >= make_date(:year_start, 1, 1)' in sql
    assert "make_date(:year_end, 1, 1) + INTERVAL '1 year'" in sql
    assert 'EXTRACT(YEAR' not in sql


def test_filters_compose():
    sql = str(pss.build_sampling_query(10, with_conf_max=True,
                                       month_mode='range', with_year_range=True))
    for frag in (':conf_max', ':month_start', ':year_start', ':year_end'):
        assert frag in sql
    # ...without disturbing the parts the rest of the pipeline relies on
    assert 'ntile(10)' in sql
    assert 'NOT EXISTS' in sql


@pytest.mark.parametrize('bad', ['both', 'RANGE', 'wrapped', 0])
def test_an_unknown_month_mode_is_refused(bad):
    with pytest.raises(ValueError):
        pss.build_sampling_query(10, month_mode=bad)


# ── run_stratified_sample: value → shape decisions ───────────────────────────

def _mock_conn():
    conn = MagicMock()
    res = MagicMock()
    res.mappings.return_value.fetchall.return_value = []
    conn.execute.return_value = res
    return conn


def _executed(conn):
    """(sql_text, params) of the single query the sampler ran."""
    sql, params = conn.execute.call_args[0]
    return str(sql), params


def test_omitted_filters_are_not_bound():
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn)
    sql, params = _executed(conn)
    for key in ('conf_max', 'month_start', 'month_end', 'year_start', 'year_end'):
        assert key not in params
    assert 'EXTRACT(MONTH' not in sql


def test_a_confidence_band_binds_both_ends():
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn,
                              confidence_threshold=0.3, confidence_max=0.6)
    sql, params = _executed(conn)
    assert params['conf_thr'] == 0.3 and params['conf_max'] == 0.6
    assert ':conf_max' in sql


def test_a_february_to_april_window_is_a_plain_range():
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn,
                              month_start=2, month_end=4)
    sql, params = _executed(conn)
    assert params['month_start'] == 2 and params['month_end'] == 4
    assert 'BETWEEN :month_start AND :month_end' in sql


def test_a_november_to_february_window_wraps_the_year():
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn,
                              month_start=11, month_end=2)
    sql, params = _executed(conn)
    assert params['month_start'] == 11 and params['month_end'] == 2
    assert ' OR EXTRACT(MONTH' in sql


def test_a_full_year_window_costs_no_clause():
    """Jan-Dec is every month, so the EXTRACT is pointless work."""
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn,
                              month_start=1, month_end=12)
    sql, params = _executed(conn)
    assert 'EXTRACT(MONTH' not in sql
    assert 'month_start' not in params


def test_half_a_month_window_is_ignored_rather_than_guessed():
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn, month_start=3)
    sql, _params = _executed(conn)
    assert 'EXTRACT(MONTH' not in sql


def test_a_year_range_binds_both_bounds():
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn,
                              year_start=2023, year_end=2025)
    sql, params = _executed(conn)
    assert params['year_start'] == 2023 and params['year_end'] == 2025
    assert 'make_date(:year_start, 1, 1)' in sql


def test_the_year_range_and_the_month_window_coexist():
    """The pairing is the point: years bound the scan, months pick the season."""
    conn = _mock_conn()
    pss.run_stratified_sample('Bufo bufo', [10], conn=conn,
                              month_start=2, month_end=4,
                              year_start=2023, year_end=2025)
    sql, params = _executed(conn)
    assert 'EXTRACT(MONTH' in sql and 'make_date' in sql
    assert params['month_start'] == 2 and params['year_end'] == 2025


# ── get_earliest_recording_year ───────────────────────────────────────────────

def _year_conn(earliest):
    conn = MagicMock()
    row = MagicMock()
    row.earliest = earliest
    conn.execute.return_value.fetchone.return_value = row
    return conn


@pytest.fixture(autouse=True)
def _clear_year_cache():
    pss.reset_earliest_year_cache()
    yield
    pss.reset_earliest_year_cache()


def test_earliest_year_comes_from_the_oldest_recording(app):
    conn = _year_conn(datetime(2019, 4, 2, 5, 30))
    with app.app_context():
        assert pss.get_earliest_recording_year(conn) == 2019


def test_earliest_year_is_cached_so_the_page_pays_one_query(app):
    conn = _year_conn(datetime(2019, 4, 2))
    with app.app_context():
        pss.get_earliest_recording_year(conn)
        pss.get_earliest_recording_year(conn)
        pss.get_earliest_recording_year(conn)
    assert conn.execute.call_count == 1


def test_refresh_bypasses_the_cache(app):
    conn = _year_conn(datetime(2019, 4, 2))
    with app.app_context():
        pss.get_earliest_recording_year(conn)
        pss.get_earliest_recording_year(conn, refresh=True)
    assert conn.execute.call_count == 2


def test_a_stale_cache_entry_is_re_read(app, monkeypatch):
    conn = _year_conn(datetime(2019, 4, 2))
    with app.app_context():
        pss.get_earliest_recording_year(conn)
        # pretend the TTL has elapsed
        year, cached_at = pss._earliest_year_cache
        pss._earliest_year_cache = (year, cached_at - pss.EARLIEST_YEAR_TTL_SECONDS - 1)
        pss.get_earliest_recording_year(conn)
    assert conn.execute.call_count == 2


def test_an_empty_table_falls_back_to_this_year(app):
    conn = _year_conn(None)
    with app.app_context():
        assert pss.get_earliest_recording_year(conn) == datetime.now().year


def test_a_nonsense_timestamp_cannot_drag_the_selector_to_year_one(app):
    conn = _year_conn(datetime(1, 1, 1))
    with app.app_context():
        assert pss.get_earliest_recording_year(conn) == pss.EARLIEST_YEAR_FLOOR


def test_a_db_error_degrades_to_this_year_instead_of_breaking_the_page(app):
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError('connection reset')
    with app.app_context():
        assert pss.get_earliest_recording_year(conn) == datetime.now().year


# ── the /prepare endpoint ─────────────────────────────────────────────────────

def _prepare(client, **body):
    payload = {'species_name': 'Bufo bufo', 'location_ids': [10]}
    payload.update(body)
    return client.post('/uk/api/pam/sample/prepare',
                       data=json.dumps(payload),
                       content_type='application/json')


def _patched_sampler():
    return patch('app.pam.routes.run_stratified_sample', return_value=[])


def test_prepare_forwards_the_whole_window(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, confidence_threshold=0.3, confidence_max=0.6,
                        month_start=2, month_end=4,
                        year_start=2023, year_end=2025)
    assert resp.status_code == 200
    kwargs = sampler.call_args.kwargs
    assert kwargs['confidence_threshold'] == 0.3
    assert kwargs['confidence_max'] == 0.6
    assert (kwargs['month_start'], kwargs['month_end']) == (2, 4)
    assert (kwargs['year_start'], kwargs['year_end']) == (2023, 2025)


def test_prepare_without_a_window_keeps_the_old_behaviour(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl)
    assert resp.status_code == 200
    kwargs = sampler.call_args.kwargs
    assert kwargs['confidence_max'] is None
    assert kwargs['month_start'] is None and kwargs['year_start'] is None


@pytest.mark.parametrize('conf_max', [1, 1.0, '', None])
def test_a_max_of_one_is_no_upper_bound_at_all(auth_client, conf_max):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, confidence_max=conf_max)
    assert resp.status_code == 200
    assert sampler.call_args.kwargs['confidence_max'] is None


def test_an_inverted_confidence_band_answers_400(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, confidence_threshold=0.8, confidence_max=0.4)
    assert resp.status_code == 400
    sampler.assert_not_called()


def test_an_inverted_year_range_answers_400(auth_client):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, year_start=2025, year_end=2023)
    assert resp.status_code == 400
    sampler.assert_not_called()


def test_an_inverted_month_window_is_legal(auth_client):
    """Nov to Feb is a season, not a mistake."""
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, month_start=11, month_end=2)
    assert resp.status_code == 200
    assert sampler.call_args.kwargs['month_start'] == 11


@pytest.mark.parametrize('body', [
    {'month_start': 0, 'month_end': 4},
    {'month_start': 2, 'month_end': 13},
])
def test_a_month_outside_one_to_twelve_answers_400(auth_client, body):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, **body)
    assert resp.status_code == 400
    sampler.assert_not_called()


@pytest.mark.parametrize('body', [
    {'month_start': 3},
    {'month_end': 3},
    {'year_start': 2024},
    {'year_end': 2024},
])
def test_half_a_range_answers_400_rather_than_guessing(auth_client, body):
    cl = auth_client(role='admin')
    with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
         _patched_sampler() as sampler:
        resp = _prepare(cl, **body)
    assert resp.status_code == 400
    sampler.assert_not_called()


# ── the page ──────────────────────────────────────────────────────────────────

def test_the_page_seeds_the_year_selector_from_the_archive(auth_client):
    cl = auth_client(role='admin')
    conn = MagicMock()
    res = MagicMock()
    res.mappings.return_value.fetchall.return_value = []
    res.fetchall.return_value = []
    conn.execute.return_value = res
    this_year = datetime.now().year
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.routes.get_earliest_recording_year', return_value=2019):
        resp = cl.get('/uk/pam/verification/sample-upload')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert 'su-year-start' in html and 'su-year-end' in html
    assert 'su-month-start' in html and 'su-conf-max' in html
    # every year from the oldest recording to now is offered, oldest preselected
    assert f'<option value="2019" selected>2019</option>' in html
    assert f'<option value="{this_year}" selected>{this_year}</option>' in html

def test_the_english_page_shows_translated_month_and_year_labels(auth_client):
    """The pam domain guessed these msgstrs from the weather columns on first
    merge (April -> "Wind", November -> "Precip."), so the catalog is pinned."""
    cl = auth_client(role='admin')
    conn = MagicMock()
    res = MagicMock()
    res.mappings.return_value.fetchall.return_value = []
    res.fetchall.return_value = []
    conn.execute.return_value = res
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn),          patch('app.pam.routes.get_earliest_recording_year', return_value=2019):
        resp = cl.get('/en/pam/verification/sample-upload')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    for label in ('Max. confidence', 'Month from', 'Month to',
                  'Year from', 'Year to', 'April', 'November'):
        assert label in html, label
    assert 'Wind' not in html and 'Precip.' not in html
