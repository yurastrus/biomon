"""
Shape of the /api/stats/top-species query.

The endpoint was rewritten on 2026-09-15 from aggregate-then-filter to
filter-then-aggregate. Three properties of the new statement are load-bearing,
and all three are invisible in the response body, so they are asserted against
the SQL the endpoint actually sends.

1. The date window narrows observations BEFORE the consensus aggregate. The old
   shape grouped and ranked the whole identifications ⋈ photos table and applied
   the filters afterwards, so a one-day chart cost the same as an all-time one
   (measured on prod: ~700 ms either way, versus 49 ms / 351 ms after).

2. The date predicate stays sargable. `DATE(o.series_start_time) BETWEEN ...`
   wraps the column in a function, which makes idx_observations_series_start
   unusable — the index would be there and never chosen.

3. The biotope filter uses EXISTS, not a join. 314 of 914 prod locations carry
   more than one biotope (up to six), so joining location_biotopes multiplied
   the observation rows and COUNT() counted an observation once per matching
   biotope: a three-biotope filter inflated the chart by ~26 % and changed which
   species appeared in it.

Run:
    venv/Scripts/python -m pytest tests/test_ct_top_species_query_shape.py -v
"""
import re

import pytest

URL = '/uk/camera-traps/api/stats/top-species'


@pytest.fixture
def captured_sql(app, monkeypatch):
    """Record the raw SQL of the top-species statement without changing it."""
    from app.camera_traps import routes

    seen = []
    original = routes.get_ct_session

    def _wrapped():
        session = original()
        connection = session.connection()
        real_execute = connection.execute

        def _execute(statement, *args, **kwargs):
            seen.append(' '.join(str(statement).split()))
            return real_execute(statement, *args, **kwargs)

        monkeypatch.setattr(connection, 'execute', _execute, raising=False)
        return session

    monkeypatch.setattr(routes, 'get_ct_session', _wrapped)
    return seen


def _top_species_sql(seen):
    for sql in seen:
        if 'RankedConsensus' in sql:
            return sql
    raise AssertionError(f'top-species statement not captured; got {seen!r}')


def test_observations_are_narrowed_before_the_consensus_aggregate(client, captured_sql):
    resp = client.get(URL + '?start_date=2025-01-01&end_date=2025-12-31')
    assert resp.status_code == 200
    sql = _top_species_sql(captured_sql)

    # The window lives in a CTE that the consensus aggregate joins against...
    assert 'EligibleObservations' in sql
    assert re.search(r'JOIN\s+EligibleObservations\b', sql)
    # ...and that CTE is declared before ObservationConsensus consumes it.
    assert sql.index('EligibleObservations') < sql.index('ObservationConsensus')


def test_date_predicate_is_sargable(client, captured_sql):
    resp = client.get(URL + '?start_date=2025-01-01&end_date=2025-12-31')
    assert resp.status_code == 200
    sql = _top_species_sql(captured_sql)

    assert 'DATE(o.series_start_time)' not in sql
    assert 'o.series_start_time >=' in sql
    assert 'o.series_start_time <' in sql


def test_end_date_stays_inclusive_of_the_whole_day(client, captured_sql):
    """Half-open upper bound, so the end date itself is still counted."""
    resp = client.get(URL + '?start_date=2025-01-01&end_date=2025-12-31')
    assert resp.status_code == 200
    sql = _top_species_sql(captured_sql)

    assert re.search(r'series_start_time\s*<\s*\(CAST\(:end_date AS date\)\s*\+\s*1\)', sql)


def test_biotope_filter_cannot_multiply_observations(client, captured_sql):
    resp = client.get(URL + '?biotopes=1,2,3')
    assert resp.status_code == 200
    sql = _top_species_sql(captured_sql)

    assert 'EXISTS' in sql
    assert re.search(r'EXISTS\s*\(\s*SELECT 1 FROM location_biotopes', sql)
    # No join that could fan out the observation rows.
    assert not re.search(r'JOIN\s+location_biotopes', sql)


def test_consensus_tie_break_is_deterministic(client, captured_sql):
    """Without a third ORDER BY key, ROW_NUMBER picks arbitrarily among tied
    species and the chart changes between runs (11 tied observations in the 2025
    window on prod)."""
    resp = client.get(URL)
    assert resp.status_code == 200
    sql = _top_species_sql(captured_sql)

    assert re.search(
        r'ORDER BY vote_count DESC,\s*max_quantity DESC,\s*species_id DESC', sql)


def test_species_filter_stays_outside_the_consensus(client, captured_sql):
    """`s.id > 0` decides which winners count as species. Moving it inside would
    change the vote itself, letting a species win a series it actually lost."""
    resp = client.get(URL)
    assert resp.status_code == 200
    sql = _top_species_sql(captured_sql)

    outer = sql[sql.index('FROM RankedConsensus'):]
    assert 's.id > 0' in outer
