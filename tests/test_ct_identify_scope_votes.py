"""
Identification queue under an institution scope (perf refactor, 2026-09-15).

The first-tier subquery behind `priority_random` used to run over EVERY pending
series in the database, ignoring the scope and AI filters the outer query
applies — ~600 ms of a ~770 ms request on prod. It now receives the same
narrowing predicates. These tests pin the behaviour that refactor must not
change: a scoped queue still puts identified series ahead of untouched ones,
still ignores identifications that belong to series outside the scope, and the
counter still reports both numbers from its single-pass query.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identify_scope_votes.py -v
"""
from datetime import datetime
from unittest.mock import patch

import pytest

NEXT_URL = '/uk/camera-traps/api/next-observation-for-identification'
STATS_URL = '/uk/camera-traps/api/identification-stats'

INST_IN_SCOPE = 41
INST_OUT_OF_SCOPE = 42


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _link(ct_session, location_id, institution_id):
    from app.camera_traps.models import location_institutions
    ct_session.execute(location_institutions.insert().values(
        location_id=location_id, institution_id=institution_id))
    ct_session.commit()


def _vote(ct_session, photo, user_id, species_id=None):
    from app.camera_traps.models import Identification
    ct_session.add(Identification(
        photo_id=photo.id, user_id=user_id, species_id=species_id))
    ct_session.commit()


@pytest.fixture
def two_institutions(ct_route_session, make_ct_location, make_ct_observation,
                     make_ct_photo):
    """Inside the scope: a fresh series and an identified one (2 votes).
    Outside it: a series with 3 votes, so a first-tier subquery that ignores
    the scope would still pull it into the in-scope queue."""
    sess = ct_route_session

    loc_in = make_ct_location(name='В межах установи')
    _link(sess, loc_in.id, INST_IN_SCOPE)
    loc_out = make_ct_location(name='Поза установою')
    _link(sess, loc_out.id, INST_OUT_OF_SCOPE)

    obs_fresh = make_ct_observation(location=loc_in,
                                    series_start_time=datetime(2025, 1, 1, 8, 0))
    make_ct_photo(observation=obs_fresh)

    obs_contested = make_ct_observation(location=loc_in,
                                        series_start_time=datetime(2025, 1, 2, 8, 0))
    photo_contested = make_ct_photo(observation=obs_contested)
    _vote(sess, photo_contested, user_id=97)
    _vote(sess, photo_contested, user_id=98)

    obs_outside = make_ct_observation(location=loc_out,
                                      series_start_time=datetime(2025, 1, 3, 8, 0))
    photo_outside = make_ct_photo(observation=obs_outside)
    for uid in (97, 98, 99):
        _vote(sess, photo_outside, user_id=uid)

    return {'fresh': obs_fresh, 'contested': obs_contested, 'outside': obs_outside}


def test_scoped_queue_still_prioritises_the_identified_series(
        auth_client, db_session, two_institutions):
    """Narrowing the first-tier subquery must not lose in-scope identifications:
    the identified series still beats the untouched one inside the scope."""
    cl = auth_client(role='admin')
    for _ in range(10):
        resp = cl.get(f'{NEXT_URL}?scope_institution_id={INST_IN_SCOPE}')
        assert resp.status_code == 200
        assert resp.get_json()['observation_id'] == two_institutions['contested'].id


def test_scoped_queue_never_serves_a_series_from_another_institution(
        auth_client, db_session, two_institutions):
    """The out-of-scope series carries the most votes; it must stay invisible."""
    cl = auth_client(role='admin')
    outside_id = two_institutions['outside'].id
    served = set()
    for _ in range(6):
        resp = cl.get(f'{NEXT_URL}?scope_institution_id={INST_IN_SCOPE}')
        assert resp.status_code == 200
        served.add(resp.get_json()['observation_id'])
    assert outside_id not in served


def test_unscoped_queue_reaches_identified_series_of_both_institutions(
        auth_client, db_session, two_institutions):
    """Without a scope both identified series are in the same tier and the
    untouched one stays behind them."""
    cl = auth_client(role='admin')
    served = set()
    for _ in range(25):
        resp = cl.get(NEXT_URL)
        assert resp.status_code == 200
        served.add(resp.get_json()['observation_id'])
    assert served == {two_institutions['contested'].id,
                      two_institutions['outside'].id}
    assert two_institutions['fresh'].id not in served


def test_scoped_stats_count_only_in_scope_series(auth_client, db_session,
                                                 two_institutions):
    """Both counters come from one aggregate now — check they still disagree
    correctly: 2 series remain, 1 of them already carries someone's vote."""
    cl = auth_client(role='admin')
    resp = cl.get(f'{STATS_URL}?scope_institution_id={INST_IN_SCOPE}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['remaining_count'] == 2
    assert data['already_identified_count'] == 1


def test_unscoped_stats_count_every_series(auth_client, db_session, two_institutions):
    cl = auth_client(role='admin')
    resp = cl.get(STATS_URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['remaining_count'] == 3
    assert data['already_identified_count'] == 2


def test_ai_species_list_sql_restricts_the_winning_pass_to_candidates():
    """The winning-prediction pass must read only candidate series.

    Its previous shape ran DISTINCT ON over every pending prediction in the
    database and filtered afterwards, which spilled the sort to disk. This is a
    shape assertion on purpose: the cost lives entirely in WHERE the filters sit,
    and a rewrite that moves them back out would not fail any behavioural test.
    """
    from unittest.mock import MagicMock
    from app.camera_traps.ai_runner import get_species_with_ai_predictions

    sess = MagicMock()
    sess.query.return_value.first.return_value = MagicMock(id=1)
    sess.execute.return_value.fetchall.return_value = []

    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        get_species_with_ai_predictions(user_id=1, user_inst_ids=[10],
                                        is_admin=False)

    sql = str(sess.execute.call_args.args[0])
    assert 'WITH cand AS' in sql
    assert 'ap.observation_id = ANY(ARRAY(SELECT id FROM cand))' in sql
    # The access/user filters belong to the candidate pass, not to a later stage.
    cand_block = sql.split('win AS')[0]
    assert 'i.user_id = :uid' in cand_block
    assert 'l.visibility_level = 0' in cand_block
