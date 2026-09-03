"""
Access scoping for accounts with NO institutions — the shape self-service
registration creates.

Opening registration changes what "@login_required" means: it used to imply a
hand-created account, and now means "anyone on the internet who confirmed an
address". These tests pin the rule that replaces that assumption:

    no institutions  ⇒  public locations only  (visibility_level == 0)

both for reads (listings, location details, PAM segment queue) and for writes
(camera-trap identifications), plus the role gates on the verification APIs.

Run:
    venv/Scripts/python -m pytest tests/test_public_scope_access.py -v
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from flask import g


PUBLIC = 0        # Location.visibility_level for "open to everyone"
RESTRICTED = 1


def forget_cached_user():
    g.pop('_login_user', None)


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _link_location_to_institution(ct_session, location_id, institution_id):
    from app.camera_traps.models import location_institutions
    ct_session.execute(location_institutions.insert().values(
        location_id=location_id, institution_id=institution_id))
    ct_session.commit()


# ── camera_traps.utils.can_access_location ──────────────────────────────────

def test_public_location_is_accessible_without_institutions(ct_session, make_ct_location):
    from app.camera_traps.utils import can_access_location

    loc = make_ct_location(name='Публічна', visibility_level=PUBLIC)
    assert can_access_location(ct_session, loc.id) is True


def test_restricted_location_needs_a_matching_institution(ct_session, make_ct_location):
    from app.camera_traps.utils import can_access_location

    loc = make_ct_location(name='Закрита', visibility_level=RESTRICTED)
    assert can_access_location(ct_session, loc.id) is False
    assert can_access_location(ct_session, loc.id, user_inst_ids=[7]) is False

    _link_location_to_institution(ct_session, loc.id, 7)
    assert can_access_location(ct_session, loc.id, user_inst_ids=[7]) is True
    assert can_access_location(ct_session, loc.id, user_inst_ids=[8]) is False


def test_admin_sees_restricted_locations_but_not_missing_ones(ct_session, make_ct_location):
    from app.camera_traps.utils import can_access_location

    loc = make_ct_location(visibility_level=RESTRICTED)
    assert can_access_location(ct_session, loc.id, is_admin=True) is True
    assert can_access_location(ct_session, 999999, is_admin=True) is False


def test_unknown_location_fails_closed(ct_session):
    from app.camera_traps.utils import can_access_location
    assert can_access_location(ct_session, 424242) is False


# ── CT: location details must honour visibility ─────────────────────────────

def test_location_details_hidden_for_restricted_location(auth_client, db_session,
                                                         ct_route_session, make_ct_location):
    loc = make_ct_location(name='Секретна балка', visibility_level=RESTRICTED)
    cl = auth_client(role='viewer', username='nobody')

    resp = cl.get(f'/uk/camera-traps/api/location/{loc.id}')
    assert resp.status_code == 404, 'a restricted location must not be discoverable'
    assert 'Секретна балка' not in resp.get_data(as_text=True)


def test_location_details_served_for_public_location(auth_client, db_session,
                                                    ct_route_session, make_ct_location):
    loc = make_ct_location(name='Відкрита галявина', visibility_level=PUBLIC)
    cl = auth_client(role='viewer', username='nobody2')

    resp = cl.get(f'/uk/camera-traps/api/location/{loc.id}')
    assert resp.status_code == 200
    assert json.loads(resp.data)['name'] == 'Відкрита галявина'


def test_location_details_served_to_admin(auth_client, db_session,
                                          ct_route_session, make_ct_location):
    loc = make_ct_location(name='Секретна балка', visibility_level=RESTRICTED)
    cl = auth_client(role='admin', username='root_loc')
    assert cl.get(f'/uk/camera-traps/api/location/{loc.id}').status_code == 200


# ── CT: identification is a write, and writes need scope too ────────────────

def test_identification_rejected_for_out_of_scope_series(auth_client, db_session,
                                                         ct_route_session,
                                                         make_ct_location,
                                                         make_ct_observation,
                                                         make_ct_species):
    from app.camera_traps.models import Identification

    loc = make_ct_location(name='Закрита', visibility_level=RESTRICTED)
    obs = make_ct_observation(location=loc)
    species = make_ct_species()

    cl = auth_client(role='ct_verifier', username='outsider')
    resp = cl.post('/uk/camera-traps/api/submit-identification',
                   json={'observation_id': obs.id, 'species_id': species.id})

    assert resp.status_code == 403
    assert ct_route_session.query(Identification).count() == 0


def test_identification_accepted_for_public_series(auth_client, db_session,
                                                   ct_route_session,
                                                   make_ct_location,
                                                   make_ct_observation,
                                                   make_ct_photo,
                                                   make_ct_species):
    loc = make_ct_location(name='Публічна', visibility_level=PUBLIC)
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs)
    species = make_ct_species()

    cl = auth_client(role='ct_verifier', username='volunteer')
    resp = cl.post('/uk/camera-traps/api/submit-identification',
                   json={'observation_id': obs.id, 'species_id': species.id})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert json.loads(resp.data)['success'] is True


def test_identification_queue_requires_verifier_role(auth_client, db_session,
                                                     ct_route_session):
    """A plain viewer no longer reaches the identification queue.

    The camera-traps role_required redirects (302 to the module dashboard)
    instead of aborting 403 — see app/camera_traps/decorators.py — so the assert
    is on "did not serve the queue", not on a specific error code.
    """
    cl = auth_client(role='viewer', username='plain_viewer')
    resp = cl.get('/uk/camera-traps/api/next-observation-for-identification')
    assert resp.status_code == 302
    assert '/camera-traps' in resp.headers['Location']


# ── CT: daily-activity CSV must not aggregate over everything ───────────────

def test_daily_activity_download_limits_institutionless_user_to_public(
        auth_client, db_session, ct_route_session, make_ct_location):
    """Previously `scope_type=global` for a user with no institutions meant "all
    locations" — an aggregate over restricted ones included."""
    public = make_ct_location(name='Публічна', visibility_level=PUBLIC)
    make_ct_location(name='Закрита', visibility_level=RESTRICTED)

    captured = {}

    def _fake_effort(session, start, end, location_ids=None):
        captured['location_ids'] = location_ids
        return {}

    cl = auth_client(role='viewer', username='csv_user')
    with patch('app.camera_traps.routes.calculate_total_effort', side_effect=_fake_effort), \
         patch('app.camera_traps.routes.fetch_raw_daily_data', return_value=[]):
        cl.get('/uk/camera-traps/api/stats/daily-activity/download'
               '?start_date=2025-01-01&end_date=2025-01-31&species_ids=1&scope_type=global')

    assert captured.get('location_ids') == [public.id]


# ── PAM: the segment access baseline ────────────────────────────────────────

def _segment_sql_for(app, user):
    """Return (sql, params) as built for ``user`` by _segment_access_sql."""
    from app.pam.routes import _segment_access_sql
    from flask_login import login_user

    with app.test_request_context('/'):
        login_user(user)
        return _segment_access_sql('seg')


def test_access_baseline_gives_public_segments_without_institutions(app, db_session, make_user):
    user = make_user(username='pam_no_inst', roles=('pam_verifier',))
    sql, params = _segment_sql_for(app, user)

    assert 'l_pub.visibility_level = 0' in sql, \
        'a verifier with no institutions must get the PUBLIC pool, not nothing'
    assert 'FALSE' not in sql
    assert params == {}


def test_access_baseline_adds_own_institutions(app, db_session, make_user):
    from app.models import Institution, UserInstitution

    inst = Institution(name_uk='Установа', code='ACC-1')
    db_session.add(inst)
    db_session.flush()

    user = make_user(username='pam_with_inst', roles=('pam_verifier',))
    db_session.add(UserInstitution(user_id=user.id, institution_id=inst.id, can_view_ct=True, can_view_pam=True))
    db_session.commit()

    sql, params = _segment_sql_for(app, user)
    assert 'l_pub.visibility_level = 0' in sql
    assert 'li_acc.institution_id' in sql
    assert params == {'access_inst_ids': [inst.id]}


def test_access_baseline_unrestricted_for_admin(app, db_session, make_user):
    user = make_user(username='pam_admin', roles=('admin',))
    sql, params = _segment_sql_for(app, user)
    assert sql == 'TRUE'
    assert params == {}


# ── PAM: the segment listing endpoint ───────────────────────────────────────

def _capture_pam_sql(cl, url):
    """Run a PAM endpoint against a mocked connection, returning executed SQL."""
    statements = []

    def _ex(sql, params=None):
        statements.append((str(sql), params or {}))
        res = MagicMock()
        res.fetchall.return_value = []
        res.fetchone.return_value = None
        res.scalar.return_value = 0
        return res

    conn = MagicMock()
    conn.execute.side_effect = _ex
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn), \
         patch('app.pam.utils.get_pam_db_connection', return_value=conn):
        resp = cl.get(url)
    return resp, statements


def test_segment_listing_requires_verifier_role(auth_client, db_session):
    cl = auth_client(role='viewer', username='pam_viewer')
    resp, _ = _capture_pam_sql(cl, '/uk/api/verification/segments')
    assert resp.status_code == 403


def test_segment_listing_applies_access_baseline_to_every_query(auth_client, db_session):
    """Rows, total count, per-status counts and average confidence must all be
    scoped — otherwise the counters advertise segments the user cannot open."""
    cl = auth_client(role='pam_verifier', username='pam_lister')
    resp, statements = _capture_pam_sql(cl, '/uk/api/verification/segments')

    assert resp.status_code == 200
    assert statements, 'no SQL executed'
    for sql, _params in statements:
        assert 'visibility_level = 0' in sql, f'unscoped query: {sql[:120]}'


def test_verification_stats_and_leaderboard_require_verifier_role(auth_client, db_session):
    cl = auth_client(role='viewer', username='pam_viewer2')
    for url in ('/uk/api/verification/stats',
                '/uk/pam/verification/verifiers'):
        forget_cached_user()
        resp, _ = _capture_pam_sql(cl, url)
        assert resp.status_code == 403, url
