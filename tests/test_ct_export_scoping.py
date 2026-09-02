# SPDX-License-Identifier: AGPL-3.0-only
"""Regression guard: the export query must narrow to the wanted observations
*before* it aggregates.

Background. `get_ct_occurrence_data` builds three heavy CTEs —
ObservationConsensus and WinningIdentifiers over `identifications x photos`
(≈500k x 750k rows), and AIPick over `ai_predictions` (≈760k) with a window
function. The filters that select the park (date window, location validity,
institution, QC) used to be pasted into each producer's WHERE, i.e. applied
*after* all that work. Exporting a two-location reserve therefore cost the same
as exporting the whole archive, and the planner's rows=1 estimate on the
trailing institution semi-join turned it into nested loops: one park took over
nine minutes against 84 ms once scoped.

These tests read the generated SQL rather than run it — the query needs
Postgres-only syntax, and what matters here is its shape, not its rows. The
row-level contract is covered by tests/test_data_export.py.
"""
import re
from unittest.mock import MagicMock, patch

import pytest


class _Result:
    def scalar(self):
        return 0

    def mappings(self):
        return self

    def fetchall(self):
        return []


class _Conn:
    """Records every statement instead of executing it."""

    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self.sink.append(str(statement))
        return _Result()


@pytest.fixture
def build_sql(app):
    """Return a callable that yields the SQL for a given set of filters."""
    def _build(**overrides):
        import app.camera_traps.data_export as de
        filters = {
            'species_ids': [], 'genus': None, 'family': None,
            'order': None, 'class': None,
            'start_date': '1900-01-01', 'end_date': '2026-09-02',
            'aggregation': 'none', 'aggregation_minutes': 5,
            'institution_code': 'RSNR', 'filter_type': 'species_only',
            'export_mode': 'human_ai', 'institution_ids': [20],
            'qc_exclude': [],
        }
        filters.update(overrides)
        sink = []
        engine = MagicMock()
        engine.connect.return_value = _Conn(sink)
        with app.app_context():
            with patch.object(de, 'get_ct_engine', return_value=engine):
                de.get_ct_occurrence_data(filters, limit=None)
        return sink
    return _build


def _normalise(sql):
    return re.sub(r'\s+', ' ', sql)


ALL_MODES = ['consensus', 'human_any', 'human_ai']


@pytest.mark.parametrize('mode', ALL_MODES)
def test_scope_cte_is_always_present(build_sql, mode):
    for sql in build_sql(export_mode=mode):
        assert 'ScopedObs AS (' in sql, f'{mode}: scope CTE missing'


@pytest.mark.parametrize('mode', ALL_MODES)
def test_consensus_cte_joins_the_scope(build_sql, mode):
    """The aggregation must see the park's observations, not every one of them."""
    for sql in build_sql(export_mode=mode):
        body = _normalise(sql)
        start = body.index('ObservationConsensus AS (')
        segment = body[start:start + 400]
        assert 'JOIN ScopedObs' in segment, (
            f'{mode}: ObservationConsensus aggregates unscoped — this is the '
            f'regression that made a small park take nine minutes'
        )


@pytest.mark.parametrize('mode', ALL_MODES)
def test_winning_identifiers_joins_the_scope(build_sql, mode):
    for sql in build_sql(export_mode=mode):
        body = _normalise(sql)
        start = body.index('WinningIdentifiers AS (')
        assert 'JOIN ScopedObs' in body[start:start + 400], \
            f'{mode}: WinningIdentifiers aggregates unscoped'


def test_ai_pick_joins_the_scope(build_sql):
    """AIPick only exists in human_ai; it windows over the whole prediction table."""
    for sql in build_sql(export_mode='human_ai'):
        body = _normalise(sql)
        start = body.index('AIPick AS (')
        assert 'JOIN ScopedObs' in body[start:start + 600], \
            'AIPick windows over every AI prediction in the database'


def test_institution_filter_lands_in_the_scope_not_the_producers(build_sql):
    """The institution predicate is what the planner mis-estimated; it belongs
    in the scope CTE, evaluated once, not trailing each producer."""
    sql = _normalise(build_sql(institution_ids=[20])[0])
    scope = sql[sql.index('ScopedObs AS ('):sql.index('ObservationConsensus AS (')]
    assert 'location_institutions' in scope
    assert sql.count('location_institutions') == 1, \
        'institution filter duplicated across producers again'


def test_qc_exclusion_lands_in_the_scope(build_sql):
    sql = _normalise(build_sql(qc_exclude=['qc_stolen'])[0])
    scope = sql[sql.index('ScopedObs AS ('):sql.index('ObservationConsensus AS (')]
    assert 'qc_stolen' in scope


def test_no_institution_filter_still_builds_a_scope(build_sql):
    """Admin exporting everything: the scope stays, minus the institution test."""
    sql = _normalise(build_sql(institution_ids=None)[0])
    assert 'ScopedObs AS (' in sql
    assert 'location_institutions' not in sql


@pytest.mark.parametrize('ftype', ['species_only', 'all'])
def test_species_predicate_stays_with_the_producers(build_sql, ftype):
    """`species` is not joined in the scope CTE, so its predicate cannot move
    there — guard against a well-meant future edit that tries."""
    sql = _normalise(build_sql(filter_type=ftype)[0])
    scope = sql[sql.index('ScopedObs AS ('):sql.index('ObservationConsensus AS (')]
    assert 's.id' not in scope


@pytest.mark.parametrize('agg', ['none', 'location_day', 'location_timewindow'])
def test_scoping_survives_every_aggregation_mode(build_sql, agg):
    for sql in build_sql(aggregation=agg):
        assert 'ScopedObs AS (' in sql
