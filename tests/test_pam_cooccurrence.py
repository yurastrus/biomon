# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the simultaneous-detections (co-occurrence) numeric core.

The point of the analysis is a LOWER BOUND on individuals calling at once, so
the tests check the two properties that bound has to keep: the spacing rule is
really enforced (no two counted points closer than the minimum distance), and
the count never exceeds the number of detecting locations.
"""

import datetime as dt

import numpy as np
import pytest

from app.pam import cooccurrence as cooc


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def test_pick_utm_crs_lviv_is_zone_34n():
    crs = cooc.pick_utm_crs([23.64, 23.77], [49.886, 49.980])
    assert crs.to_epsg() == 32634


def test_project_distance_matches_metres():
    """Two points 0.01 degrees of latitude apart are ~1.11 km."""
    crs = cooc.pick_utm_crs([23.7, 23.7], [49.90, 49.91])
    x, y = cooc.project([23.7, 23.7], [49.90, 49.91], crs)
    d = float(np.hypot(x[1] - x[0], y[1] - y[0]))
    assert 1090 < d < 1130


# --------------------------------------------------------------------------
# The spacing rule
# --------------------------------------------------------------------------

def test_greedy_spaced_subset_drops_the_near_point():
    # A and B are 1000 m apart; C sits 100 m from A.
    xy = {'A': (0.0, 0.0), 'B': (1000.0, 0.0), 'C': (100.0, 0.0)}
    keep = cooc.greedy_spaced_subset(['A', 'B', 'C'], xy, 500.0)
    assert keep == ['A', 'B']


def test_greedy_spaced_subset_respects_input_order():
    """Input order is "most detections first", so the loudest point survives."""
    xy = {'A': (0.0, 0.0), 'C': (100.0, 0.0)}
    assert cooc.greedy_spaced_subset(['C', 'A'], xy, 500.0) == ['C']


def test_greedy_spaced_subset_zero_distance_keeps_everything():
    xy = {'A': (0.0, 0.0), 'C': (1.0, 0.0)}
    assert cooc.greedy_spaced_subset(['A', 'C'], xy, 0.0) == ['A', 'C']


def test_greedy_subset_is_pairwise_separated_on_random_points():
    rng = np.random.default_rng(20260910)
    pts = rng.uniform(0, 3000, size=(40, 2))
    xy = {i: (float(pts[i, 0]), float(pts[i, 1])) for i in range(40)}
    keep = cooc.greedy_spaced_subset(list(range(40)), xy, 600.0)
    for i, a in enumerate(keep):
        for b in keep[i + 1:]:
            assert np.hypot(xy[a][0] - xy[b][0], xy[a][1] - xy[b][1]) >= 600.0


# --------------------------------------------------------------------------
# Clustering variants
# --------------------------------------------------------------------------

def test_single_linkage_chains_but_complete_linkage_does_not():
    """Three points in a line, 900 m apart each, at a 1000 m threshold.

    Single linkage merges all three into one cluster spanning 1800 m, which is
    exactly the chaining that destroys the signal in a dense network. Complete
    linkage bounds the cluster diameter, so it cannot.
    """
    ids = [1, 2, 3]
    x = np.array([0.0, 900.0, 1800.0])
    y = np.array([0.0, 0.0, 0.0])

    labels_single, _ = cooc.cluster_locations_single(ids, x, y, 1000.0)
    labels_complete, _ = cooc.cluster_locations_complete(ids, x, y, 1000.0)

    assert len(set(labels_single.tolist())) == 1
    assert len(set(labels_complete.tolist())) > 1


def test_close_matrix_has_no_self_conflict():
    x = np.array([0.0, 100.0, 5000.0])
    y = np.zeros(3)
    close = cooc.close_matrix(x, y, 500.0)
    assert not close.diagonal().any()
    assert close[0, 1] and close[1, 0]
    assert not close[0, 2]


def test_close_matrix_zero_distance_is_all_false():
    close = cooc.close_matrix(np.zeros(3), np.zeros(3), 0.0)
    assert not close.any()


def test_independent_set_on_a_star_keeps_one_leaf_pair():
    """0 conflicts with 1 and 2; 1 and 2 do not conflict with each other."""
    close = np.zeros((3, 3), bool)
    close[0, 1] = close[1, 0] = True
    close[0, 2] = close[2, 0] = True
    keep = cooc.independent_set([0, 1, 2], close)
    assert keep == [1, 2]


# --------------------------------------------------------------------------
# Window counting
# --------------------------------------------------------------------------

def _windows(**by_loc):
    win = dt.datetime(2025, 5, 8, 20, 17, tzinfo=dt.timezone.utc)
    return {win: {int(k[1:]): v for k, v in by_loc.items()}}, win


def test_count_windows_greedy_suppresses_the_close_location():
    per_window, win = _windows(l1=10, l2=5, l3=1)
    xy = {1: (0.0, 0.0), 2: (1000.0, 0.0), 3: (100.0, 0.0)}
    loc2cluster = {1: 0, 2: 1, 3: 2}
    selected, summary = cooc.count_windows(per_window, xy, loc2cluster, 500.0, 'greedy')
    assert selected[win] == [1, 2]
    assert summary[0]['n_counted'] == 2
    assert summary[0]['n_locations_raw'] == 3
    assert summary[0]['n_detections'] == 16


def test_count_windows_cluster_method_keeps_the_loudest_representative():
    per_window, win = _windows(l1=2, l3=9)
    xy = {1: (0.0, 0.0), 3: (100.0, 0.0)}
    loc2cluster = {1: 0, 3: 0}          # same acoustic cluster
    selected, summary = cooc.count_windows(per_window, xy, loc2cluster, 500.0, 'complete')
    assert selected[win] == [3]
    assert summary[0]['n_counted'] == 1


def test_count_windows_never_counts_more_than_the_detecting_locations():
    per_window, win = _windows(l1=1, l2=1, l3=1)
    xy = {1: (0.0, 0.0), 2: (1000.0, 0.0), 3: (2000.0, 0.0)}
    loc2cluster = {1: 0, 2: 1, 3: 2}
    _, summary = cooc.count_windows(per_window, xy, loc2cluster, 500.0, 'greedy')
    assert summary[0]['n_counted'] <= summary[0]['n_locations_raw']


# --------------------------------------------------------------------------
# Window-width sweep
# --------------------------------------------------------------------------

def test_max_curve_needs_a_wide_enough_window_to_see_the_pair():
    """Two far-apart locations 30 s apart: one window has to span the gap."""
    close = np.zeros((2, 2), bool)          # far enough apart
    rows = cooc.max_curve([0.0, 30.0], [0, 1], close, [10, 31])
    by_width = {r['width_s']: r for r in rows}
    assert by_width[10]['max_counted'] == 1
    assert by_width[31]['max_counted'] == 2


def test_max_curve_counted_is_monotonic_in_width():
    rng = np.random.default_rng(7)
    times = np.sort(rng.uniform(0, 600, size=60))
    locs = rng.integers(0, 4, size=60)
    close = np.zeros((4, 4), bool)
    rows = cooc.max_curve(times, locs, close, [10, 30, 60, 120, 300])
    counted = [r['max_counted'] for r in rows]
    assert counted == sorted(counted)


def test_max_curve_spacing_rule_caps_the_count():
    """All four locations conflict with each other, so the bound stays at 1."""
    close = np.ones((4, 4), bool)
    np.fill_diagonal(close, False)
    rows = cooc.max_curve([0.0, 1.0, 2.0, 3.0], [0, 1, 2, 3], close, [60])
    assert rows[0]['max_counted'] == 1
    assert rows[0]['max_raw'] == 4


def test_max_curve_reports_where_the_maximum_occurs():
    close = np.zeros((2, 2), bool)
    rows = cooc.max_curve([0.0, 100.0, 105.0], [0, 0, 1], close, [10])
    assert rows[0]['max_counted'] == 2
    assert rows[0]['argmax_time_s'] == 100.0


def test_pair_min_width_is_the_smallest_observed_gap():
    gaps = cooc.pair_min_width({0: [0.0, 100.0], 1: [97.0, 500.0]})
    assert gaps[(0, 1)] == pytest.approx(3.0)


def test_pair_min_width_ignores_empty_series():
    gaps = cooc.pair_min_width({0: [1.0], 1: []})
    assert gaps == {}


# --------------------------------------------------------------------------
# Parameter validation, without touching a database
# --------------------------------------------------------------------------

class _FakeConn:
    """Just enough of a connection for the guard clauses to be reached."""

    def __init__(self, species_row=(42, 'Glaucidium passerinum')):
        self.species_row = species_row

    def execute(self, *_a, **_kw):
        conn = self

        class _R:
            def fetchone(self_inner):
                return conn.species_row

            def fetchall(self_inner):
                return []

            def mappings(self_inner):
                return self_inner

            def scalar(self_inner):
                return 0
        return _R()


def _run(**over):
    kw = dict(
        species='42',
        start=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
        end=dt.datetime(2025, 2, 1, tzinfo=dt.timezone.utc),
        window_s=60, step_s=60, min_distance_m=500.0,
        cluster_method='greedy', min_conf=0.8, conf_column='confidence',
        access_condition='1=1', access_params={},
    )
    kw.update(over)
    return cooc.run(_FakeConn(), **kw)


def test_run_rejects_a_reversed_period():
    with pytest.raises(cooc.CooccurrenceError):
        _run(end=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))


def test_run_rejects_a_step_wider_than_the_window():
    with pytest.raises(cooc.CooccurrenceError):
        _run(window_s=60, step_s=120)


def test_run_rejects_too_many_database_passes():
    with pytest.raises(cooc.CooccurrenceError) as e:
        _run(window_s=3600, step_s=1)
    assert 'проход' in str(e.value)


def test_run_rejects_an_unknown_cluster_method():
    with pytest.raises(cooc.CooccurrenceError):
        _run(cluster_method='kmeans')


def test_run_reports_when_no_location_passes_the_filters():
    with pytest.raises(cooc.CooccurrenceError) as e:
        _run()
    assert 'локац' in str(e.value)


def test_resolve_species_rejects_an_unknown_name():
    class _Empty(_FakeConn):
        def __init__(self):
            super().__init__(species_row=None)

    with pytest.raises(cooc.CooccurrenceError):
        cooc.resolve_species(_Empty(), 'Nonexistent species')


def test_resolve_species_rejects_an_empty_token():
    with pytest.raises(cooc.CooccurrenceError):
        cooc.resolve_species(_FakeConn(), '')


def test_fetch_window_table_rejects_a_non_whitelisted_score_column():
    """The column name is interpolated into SQL, so the whitelist is the guard."""
    with pytest.raises(cooc.CooccurrenceError):
        cooc.fetch_window_table(
            _FakeConn(), 1,
            dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
            dt.datetime(2025, 2, 1, tzinfo=dt.timezone.utc),
            60, dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
            0.8, 'confidence; DROP TABLE detections', [1],
        )


def test_run_sweep_rejects_too_many_widths():
    with pytest.raises(cooc.CooccurrenceError) as e:
        cooc.run_sweep(
            _FakeConn(), species='42',
            start=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
            end=dt.datetime(2025, 2, 1, tzinfo=dt.timezone.utc),
            min_distance_m=500.0, min_conf=0.8, conf_column='confidence',
            access_condition='1=1', access_params={},
            sweep_min_s=1, sweep_max_s=600, sweep_step_s=1,
        )
    assert 'ширин' in str(e.value)


# --------------------------------------------------------------------------
# The page itself
# --------------------------------------------------------------------------

def test_cooccurrence_page_is_admin_only(client):
    """Anonymous visitors are redirected to the login page, not served."""
    resp = client.get('/uk/pam/cooccurrence')
    assert resp.status_code in (302, 401, 403)


def test_hub_page_hides_the_card_from_anonymous_visitors(client, mock_pam_conn):
    with mock_pam_conn():
        resp = client.get('/uk/pam')
    assert resp.status_code == 200
    assert '/pam/cooccurrence' not in resp.get_data(as_text=True)


def test_hub_page_shows_the_card_to_an_admin(auth_client, mock_pam_conn):
    with mock_pam_conn():
        resp = auth_client(role='admin').get('/uk/pam')
    assert resp.status_code == 200
    assert '/pam/cooccurrence' in resp.get_data(as_text=True)


def test_cooccurrence_page_renders_for_an_admin_without_computing(
        auth_client, mock_pam_conn, monkeypatch):
    """Opening the page must not run the analysis, only the cheap lookups."""
    from unittest.mock import MagicMock

    conn = MagicMock()
    row = MagicMock()
    row.fetchone.return_value = None
    conn.execute.return_value = row
    monkeypatch.setattr('app.pam.routes.get_pam_db_connection', lambda: conn)
    monkeypatch.setattr('app.pam.routes.get_models_list', lambda: [])
    monkeypatch.setattr('app.pam.utils.get_available_species', lambda lang: [
        {'value': 'Glaucidium passerinum', 'text': 'Сичик-горобець (Glaucidium passerinum)'},
    ])

    called = []
    monkeypatch.setattr('app.pam.cooccurrence.run',
                        lambda *a, **kw: called.append('run'))

    with mock_pam_conn():
        resp = auth_client(role='admin').get('/uk/pam/cooccurrence')

    assert resp.status_code == 200
    assert called == []
    body = resp.get_data(as_text=True)
    assert 'cooccurrence/run' in body      # the button knows where to POST
    assert 'id="run-btn"' in body

def test_page_hands_a_csrf_token_to_its_post_calls(
        auth_client, mock_pam_conn, monkeypatch):
    """Regression: /run and /sweep are POST, so CSRFProtect rejects them without
    an X-CSRFToken header. Missing it returned a bare 400 whose body was not
    even JSON, so the page showed nothing useful."""
    from unittest.mock import MagicMock

    conn = MagicMock()
    row = MagicMock()
    row.fetchone.return_value = None
    conn.execute.return_value = row
    monkeypatch.setattr('app.pam.routes.get_pam_db_connection', lambda: conn)
    monkeypatch.setattr('app.pam.routes.get_models_list', lambda: [])
    monkeypatch.setattr('app.pam.utils.get_available_species', lambda lang: [
        {'value': 'Glaucidium passerinum', 'text': 'Glaucidium passerinum'},
    ])

    with mock_pam_conn():
        body = auth_client(role='admin').get('/uk/pam/cooccurrence').get_data(as_text=True)

    assert 'X-CSRFToken' in body
    assert body.count('headers: POST_HEADERS') == 2

# --------------------------------------------------------------------------
# Season window: the same calendar stretch pooled across every year
# --------------------------------------------------------------------------

def test_mmdd_accepts_a_date_a_string_and_an_int():
    assert cooc.mmdd(dt.date(2025, 4, 1)) == 401
    assert cooc.mmdd('2025-04-01') == 401
    assert cooc.mmdd('04-01') == 401
    assert cooc.mmdd(401) == 401
    assert cooc.mmdd(None) is None
    assert cooc.mmdd('') is None


def test_mmdd_rejects_an_impossible_boundary():
    with pytest.raises(cooc.CooccurrenceError):
        cooc.mmdd('13-40')


def test_season_clause_is_empty_without_both_boundaries():
    assert cooc.season_clause('ts', None, None) == ''
    assert cooc.season_clause('ts', 401, None) == ''
    assert cooc.season_clause('ts', None, 520) == ''


def test_season_clause_uses_a_range_when_it_does_not_wrap():
    sql = cooc.season_clause('ts', 401, 520)
    assert 'BETWEEN :season_from AND :season_to' in sql
    assert ' OR ' not in sql


def test_season_clause_splits_a_window_that_wraps_the_new_year():
    """1 Dec to 15 Feb is the union of two ranges, not an interval."""
    sql = cooc.season_clause('ts', 1201, 215)
    assert ' OR ' in sql
    assert '>= :season_from' in sql and '<= :season_to' in sql


def test_season_expression_compares_month_and_day_not_day_of_year():
    """Day-of-year shifts by one after 29 February, which would silently move
    the window in leap years."""
    sql = cooc.season_clause('ts', 401, 520)
    assert 'extract(month' in sql and 'extract(day' in sql
    assert 'doy' not in sql


# --------------------------------------------------------------------------
# Human verification
# --------------------------------------------------------------------------

def test_verification_level_two_for_consensus_or_two_votes():
    assert cooc.verification_level(0, 1, 0) == 2      # consensus on a detection
    assert cooc.verification_level(2, 0, 0) == 2      # two positive votes
    assert cooc.verification_level(5, 3, 1) == 2      # consensus wins over a reject


def test_verification_level_one_for_a_single_positive_vote():
    assert cooc.verification_level(1, 0, 0) == 1


def test_verification_level_zero_when_nobody_listened():
    assert cooc.verification_level(0, 0, 0) == 0


def test_verification_level_negative_when_people_rejected_it():
    assert cooc.verification_level(0, 0, 2) == -1


def test_window_sql_reads_verification_through_the_authoritative_map():
    """segments.detection_id (via detection_verification_map) is the only
    sanctioned link; filename/datetime heuristics are not used here."""
    sql = cooc.WINDOW_SQL
    assert 'detection_verification_map' in sql
    assert 'dvm.detection_id = d.detection_id' in sql
    assert 'positive_verifications' in sql
    assert 'recorded_date' not in sql and 'location_name' not in sql


# --------------------------------------------------------------------------
# Window width bounds
# --------------------------------------------------------------------------

def test_window_bounds_are_ten_seconds_to_one_hour():
    assert cooc.WINDOW_MIN_S == 10
    assert cooc.WINDOW_MAX_S == 3600


def test_default_confidence_threshold_is_strict():
    """A false positive at two distant points in the same minute fabricates a
    simultaneous pair, so the default leans strict rather than inclusive."""
    assert cooc.DEFAULT_MIN_CONF == 0.95


# --------------------------------------------------------------------------
# The page's new controls
# --------------------------------------------------------------------------

def _admin_page(auth_client, mock_pam_conn, monkeypatch, institutions=()):
    from unittest.mock import MagicMock

    conn = MagicMock()
    row = MagicMock()
    row.fetchone.return_value = None
    conn.execute.return_value = row
    monkeypatch.setattr('app.pam.routes.get_pam_db_connection', lambda: conn)
    monkeypatch.setattr('app.pam.routes.get_models_list', lambda: [])
    monkeypatch.setattr('app.pam.utils.get_available_species', lambda lang: [
        {'value': 'Glaucidium passerinum', 'text': 'Glaucidium passerinum'},
    ])
    with mock_pam_conn():
        return auth_client(role='admin').get('/uk/pam/cooccurrence').get_data(as_text=True)


def test_page_offers_a_numeric_window_field_within_bounds(
        auth_client, mock_pam_conn, monkeypatch):
    body = _admin_page(auth_client, mock_pam_conn, monkeypatch)
    assert 'id="window-input"' in body
    assert 'min="10"' in body and 'max="3600"' in body
    assert 'id="window-select"' not in body


def test_page_offers_the_season_window_as_decade_pickers(
        auth_client, mock_pam_conn, monkeypatch):
    body = _admin_page(auth_client, mock_pam_conn, monkeypatch)
    assert 'id="season-from"' in body and 'id="season-to"' in body
    # 36 decades in each of the two lists, and no bare date input any more.
    assert body.count('декада') == 72
    assert 'id="season-from" class="form-control">' not in body.replace(
        '<select id="season-from" class="form-control">', '')
    # The "from" list offers first days, the "to" list last days.
    assert 'value="401"' in body and 'value="520"' in body


def test_page_offers_a_sortable_verified_column(auth_client, mock_pam_conn, monkeypatch):
    body = _admin_page(auth_client, mock_pam_conn, monkeypatch)
    for key in ('start', 'n_counted', 'n_verified', 'n_locations_raw', 'n_detections'):
        assert f'data-sort="{key}"' in body


def test_page_renders_the_ecoregion_picker_with_its_institutions(
        auth_client, mock_pam_conn, monkeypatch, db_session):
    """Ecoregions come from the host `institutions` table, and each option
    carries the ids it expands to so the biotope/location cascade can narrow."""
    from app.models import Institution

    inst = Institution(name_uk='Тестова установа', code='TST-ECO',
                       ecoregion_uk='Розточчя', ecoregion_en='Roztochia')
    db_session.add(inst)
    db_session.commit()

    body = _admin_page(auth_client, mock_pam_conn, monkeypatch)
    assert 'id="ecoregion-select"' in body
    assert 'Розточчя' in body
    assert f'data-institutions="{inst.id}"' in body


def test_ecoregion_expands_to_institution_ids(app, db_session):
    """The union is what the two pickers read as: an ecoregion plus an
    institution outside it means both."""
    from app.models import Institution
    from app.pam.routes import _cooc_institution_ids

    a = Institution(name_uk='A', code='ECO-A', ecoregion_uk='Полісся')
    b = Institution(name_uk='B', code='ECO-B', ecoregion_uk='Полісся')
    c = Institution(name_uk='C', code='ECO-C', ecoregion_uk='Степ')
    db_session.add_all([a, b, c])
    db_session.commit()

    with app.test_request_context('/uk/pam/cooccurrence'):
        ids = _cooc_institution_ids({'institution_ids': [c.id],
                                     'ecoregions': ['Полісся']})
    assert set(ids) == {a.id, b.id, c.id}


def test_no_ecoregion_selected_leaves_the_institution_picks_alone(app, db_session):
    from app.pam.routes import _cooc_institution_ids

    with app.test_request_context('/uk/pam/cooccurrence'):
        assert _cooc_institution_ids({'institution_ids': [7, 3],
                                      'ecoregions': []}) == [3, 7]

# --------------------------------------------------------------------------
# Season decades
# --------------------------------------------------------------------------

def test_season_decades_has_three_per_month():
    decades = cooc.season_decades()
    assert len(decades) == 36
    assert [d['decade'] for d in decades[:3]] == [1, 2, 3]
    assert {d['month'] for d in decades} == set(range(1, 13))


def test_season_decade_boundaries_are_first_and_last_day():
    by_key = {(d['month'], d['decade']): d for d in cooc.season_decades()}
    april_1 = by_key[(4, 1)]
    may_2 = by_key[(5, 2)]
    assert (april_1['from_mmdd'], april_1['to_mmdd']) == (401, 410)
    assert (may_2['from_mmdd'], may_2['to_mmdd']) == (511, 520)
    # The user's worked example: April decade 1 to May decade 2 is 1 Apr - 20 May.
    assert cooc.season_clause('ts', april_1['from_mmdd'], may_2['to_mmdd'])


def test_third_decade_runs_to_the_end_of_its_month():
    by_key = {(d['month'], d['decade']): d for d in cooc.season_decades()}
    assert by_key[(1, 3)]['to_day'] == 31
    assert by_key[(4, 3)]['to_day'] == 30
    # February takes 29, so the window covers 29 February in leap years and
    # simply matches nothing on that day in ordinary ones.
    assert by_key[(2, 3)]['to_day'] == 29


def test_decade_values_round_trip_through_mmdd():
    """The select sends the boundary as a bare MM*100+DD integer."""
    for d in cooc.season_decades():
        assert cooc.mmdd(str(d['from_mmdd'])) == d['from_mmdd']
        assert cooc.mmdd(str(d['to_mmdd'])) == d['to_mmdd']


def test_season_narrows_the_period_and_does_not_replace_it():
    """The date filter keeps its full effect: the season clause is an extra AND
    inside the period, never a substitute for it."""
    sql = cooc.WINDOW_SQL.format(
        conf='confidence',
        season=cooc.season_clause(cooc.DET_TS_SQL, 401, 520),
    )
    assert 'r.datetime_start >= CAST(:start AS timestamptz)' in sql
    assert 'r.datetime_start <  CAST(:end AS timestamptz)' in sql
    assert 'window_start >= CAST(:start AS timestamptz)' in sql
    assert ':season_from' in sql and ':season_to' in sql
