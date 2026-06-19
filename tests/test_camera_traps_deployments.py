"""
Tests for the Deployment model and interval-based observation matching.

Cover:
  - creating a deployment and its link to Location;
  - defaults (history_unknown=False);
  - is_usable() with the "NULL = usable" rule;
  - querying observations by overlap with deployment dates;
  - the "worst case" rule when two deployments overlap.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import func

from app.camera_traps.models import Deployment, Observation


def test_create_deployment_and_location_link(make_ct_location, make_ct_deployment):
    loc = make_ct_location(name='Карпати X')
    dep = make_ct_deployment(location=loc, name='2025_Summer_X_1002', camera_id='1002')

    assert dep.id is not None
    assert dep.location_id == loc.id
    assert dep.location is loc
    assert dep in loc.deployments
    # camera_id is stored as a string (leading zeros)
    assert dep.camera_id == '1002'


def test_camera_id_preserves_leading_zero(make_ct_deployment):
    dep = make_ct_deployment(name='2025_Summer_Drevlianskyi_0405', camera_id='0405')
    assert dep.camera_id == '0405'


def test_history_unknown_defaults_false(make_ct_deployment):
    dep = make_ct_deployment()
    assert dep.history_unknown is False


def test_is_usable_null_treated_as_usable(make_ct_deployment):
    dep = make_ct_deployment()  # qc_data_not_usable not set → NULL
    assert dep.qc_data_not_usable is None
    assert dep.is_usable() is True


def test_is_usable_false_when_flagged(make_ct_deployment):
    dep = make_ct_deployment(qc_data_not_usable=True)
    assert dep.is_usable() is False


def test_observations_within_deployment_interval(ct_session, make_ct_location,
                                                  make_ct_deployment, make_ct_observation):
    loc = make_ct_location()
    make_ct_deployment(location=loc, start_date=date(2025, 7, 1), end_date=date(2025, 9, 5))

    inside = make_ct_observation(location=loc,
                                 series_start_time=datetime(2025, 8, 1, 10, 0))
    outside = make_ct_observation(location=loc,
                                  series_start_time=datetime(2025, 10, 1, 10, 0))

    dep = ct_session.query(Deployment).filter_by(location_id=loc.id).one()
    matched = ct_session.query(Observation).filter(
        Observation.location_id == loc.id,
        func.date(Observation.series_start_time) >= dep.start_date,
        func.date(Observation.series_start_time) <= dep.end_date,
    ).all()

    ids = {o.id for o in matched}
    assert inside.id in ids
    assert outside.id not in ids


def test_n_days_calc_column_present(ct_session, make_ct_deployment):
    """Generated column exists and does not break inserts (value is Postgres-only)."""
    dep = make_ct_deployment(start_date=date(2025, 7, 1), end_date=date(2025, 9, 5))
    fetched = ct_session.query(Deployment).get(dep.id)
    assert hasattr(fetched, 'n_days_calc')  # column present in model/DB


def test_count_photos_within_interval(ct_session, make_ct_location, make_ct_deployment,
                                      make_ct_observation, make_ct_photo):
    loc = make_ct_location()
    dep = make_ct_deployment(location=loc, start_date=date(2025, 7, 1),
                             end_date=date(2025, 9, 5))

    obs_in = make_ct_observation(location=loc,
                                 series_start_time=datetime(2025, 8, 1, 10, 0))
    obs_out = make_ct_observation(location=loc,
                                  series_start_time=datetime(2025, 10, 1, 10, 0))
    make_ct_photo(observation=obs_in, captured_at=datetime(2025, 8, 1, 10, 0))
    make_ct_photo(observation=obs_in, captured_at=datetime(2025, 8, 2, 10, 0))
    make_ct_photo(observation=obs_out, captured_at=datetime(2025, 10, 1, 10, 0))

    assert dep.count_photos(ct_session) == 2


def test_count_photos_open_interval(ct_session, make_ct_location, make_ct_deployment,
                                    make_ct_observation, make_ct_photo):
    """With no dates, the deployment counts all grouped photos of the location."""
    loc = make_ct_location()
    dep = make_ct_deployment(location=loc, start_date=None, end_date=None)
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs)
    make_ct_photo(observation=obs)
    assert dep.count_photos(ct_session) == 2


def test_apply_deployment_fields_coercion():
    """The field-applying helper coerces types correctly (no DB)."""
    from datetime import date, time
    from app.camera_traps.routes import _apply_deployment_fields
    dep = Deployment(location_id=1, name='orig')
    _apply_deployment_fields(dep, {
        'name': '  D1  ', 'study_year': '2025', 'n_photos': 42,
        'camera_id': '0405', 'start_date': '2025-07-01', 'start_time': '10:14',
        'qc_data_not_usable': True, 'qc_comment': '  note  ',
        'study_design': '',  # empty -> None
    })
    assert dep.name == 'D1'
    assert dep.study_year == 2025
    assert dep.n_photos == 42
    assert dep.camera_id == '0405'
    assert dep.start_date == date(2025, 7, 1)
    assert dep.start_time == time(10, 14)
    assert dep.qc_data_not_usable is True
    assert dep.qc_comment == 'note'
    assert dep.study_design is None


def test_apply_deployment_fields_keeps_name_when_blank():
    from app.camera_traps.routes import _apply_deployment_fields
    dep = Deployment(location_id=1, name='keepme')
    _apply_deployment_fields(dep, {'name': '   '})
    assert dep.name == 'keepme'


def test_apply_deployment_fields_bad_date_raises():
    from app.camera_traps.routes import _apply_deployment_fields
    dep = Deployment(location_id=1, name='x')
    with pytest.raises(ValueError):
        _apply_deployment_fields(dep, {'start_date': 'not-a-date'})


def test_worst_case_on_overlap(ct_session, make_ct_location, make_ct_deployment):
    """A frame falling into two deployments, one of them unusable → exclude."""
    loc = make_ct_location()
    make_ct_deployment(location=loc, name='good',
                       start_date=date(2025, 7, 1), end_date=date(2025, 9, 10),
                       qc_data_not_usable=False)
    make_ct_deployment(location=loc, name='bad',
                       start_date=date(2025, 9, 5), end_date=date(2025, 12, 1),
                       qc_data_not_usable=True)

    capture = date(2025, 9, 7)  # in the overlap zone
    overlapping = ct_session.query(Deployment).filter(
        Deployment.location_id == loc.id,
        Deployment.start_date <= capture,
        Deployment.end_date >= capture,
    ).all()
    assert len(overlapping) == 2

    # "Worst case" rule: usable only if NONE of them excludes it
    usable = all(d.is_usable() for d in overlapping)
    assert usable is False
