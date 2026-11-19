"""
CT identification counter: GET /api/identification-stats returns both
`remaining_count` (series still to identify by this user) and
`already_identified_count` (the subset that already carries someone else's
identification — the "(N)" shown next to the main counter).

Run:
    venv/Scripts/python -m pytest tests/test_ct_identification_stats.py -v
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/api/identification-stats'
NEXT_URL = '/uk/camera-traps/api/next-observation-for-identification'


@pytest.fixture
def ct_route_session(ct_session):
    """Back camera_traps.routes with the real SQLite CT session."""
    with patch('app.camera_traps.routes.get_ct_session',
               return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def _vote(ct_session, photo, user_id, species_id=None):
    from app.camera_traps.models import Identification
    ct_session.add(Identification(
        photo_id=photo.id, user_id=user_id, species_id=species_id))
    ct_session.commit()


def _admin_id(db_session):
    from app.models import User
    return db_session.query(User).filter_by(username='test_admin').first().id


@pytest.fixture
def mixed_series(ct_route_session, make_ct_observation, make_ct_photo):
    """fresh (0 votes), one_vote (a vote from another user), photo handles."""
    obs_fresh = make_ct_observation(series_start_time=datetime(2025, 1, 1, 8, 0))
    make_ct_photo(observation=obs_fresh)

    obs_one = make_ct_observation(series_start_time=datetime(2025, 1, 2, 8, 0))
    photo_one = make_ct_photo(observation=obs_one)
    _vote(ct_route_session, photo_one, user_id=97, species_id=None)

    return {'fresh': obs_fresh, 'one': obs_one, 'photo_one': photo_one}


def test_stats_reports_both_counts(auth_client, db_session, mixed_series):
    """remaining = fresh + one_vote (2); already_identified = one_vote (1)."""
    cl = auth_client(role='admin')
    resp = cl.get(URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['remaining_count'] == 2
    assert data['already_identified_count'] == 1


def test_own_vote_excluded_from_both_counts(
        auth_client, db_session, mixed_series, ct_route_session):
    """Once the user votes on the already-identified series it leaves both counts."""
    cl = auth_client(role='admin')
    _vote(ct_route_session, mixed_series['photo_one'], user_id=_admin_id(db_session))
    data = cl.get(URL).get_json()
    assert data['remaining_count'] == 1          # only the fresh series left
    assert data['already_identified_count'] == 0  # nothing others-only remains


def test_already_identified_zero_when_all_fresh(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo):
    """No foreign votes anywhere -> already_identified_count is 0."""
    obs = make_ct_observation(series_start_time=datetime(2025, 1, 1, 8, 0))
    make_ct_photo(observation=obs)
    cl = auth_client(role='admin')
    data = cl.get(URL).get_json()
    assert data['remaining_count'] == 1
    assert data['already_identified_count'] == 0


def test_next_observation_flags_other_identifications(
        auth_client, db_session, mixed_series):
    """Normal-mode series payload carries has_other_identifications so the
    front end can keep the (N) counter accurate on submit."""
    cl = auth_client(role='admin')
    # priority_random serves the voted (contested-tier) series first.
    resp = cl.get(NEXT_URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['observation_id'] == mixed_series['one'].id
    assert data['has_other_identifications'] is True


def test_activity_counts_distinct_own_series_since_local_midnight(
        auth_client, db_session, ct_route_session, make_ct_observation, make_ct_photo,
        make_ct_species, monkeypatch):
    from app.camera_traps.models import Identification

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 14, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr('app.camera_traps.routes.datetime', FixedDateTime)
    cl = auth_client(role='admin')
    user_id = _admin_id(db_session)
    recent = datetime(2026, 7, 14, 0, 15)
    species = make_ct_species()
    series = make_ct_observation(status='completed')
    photo_1 = make_ct_photo(observation=series)
    photo_2 = make_ct_photo(observation=series)
    ct_route_session.add_all([
        Identification(photo_id=photo_1.id, user_id=user_id, created_at=recent),
        Identification(photo_id=photo_1.id, user_id=user_id, created_at=recent,
                       species_id=species.id),
        Identification(photo_id=photo_2.id, user_id=user_id, created_at=recent),
        Identification(photo_id=make_ct_photo().id, user_id=user_id,
                       created_at=datetime(2026, 7, 13, 21)),  # Kyiv midnight in July
        Identification(photo_id=make_ct_photo().id, user_id=user_id,
                       created_at=datetime(2026, 7, 13, 4)),  # New York midnight in July
        Identification(photo_id=make_ct_photo().id, user_id=user_id,
                       created_at=datetime(2026, 7, 13, 3, 59)),
        Identification(photo_id=make_ct_photo().id, user_id=97, created_at=recent),
    ])
    ct_route_session.commit()

    assert cl.get(URL).get_json()['identified_today'] == 1
    assert cl.get(URL + '?time_zone=Europe/Kyiv').get_json()['identified_today'] == 2
    assert cl.get(URL + '?activity_only=1&time_zone=America/New_York').get_json() == {
        'identified_today': 3,
    }
    assert cl.get(URL + '?time_zone=Invalid/Zone').get_json()['identified_today'] == 1
