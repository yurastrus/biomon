"""
Тести моделі Deployment та логіки інтервального матчингу спостережень.

Перевіряють:
  - створення деплойменту й зв'язок з Location;
  - дефолти (history_unknown=False);
  - is_usable() з правилом «NULL = придатний»;
  - запит спостережень по перекриттю дат деплойменту;
  - правило «найгірший сценарій» при перекритті двох деплойментів.
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
    # camera_id зберігається як рядок (провідні нулі)
    assert dep.camera_id == '1002'


def test_camera_id_preserves_leading_zero(make_ct_deployment):
    dep = make_ct_deployment(name='2025_Summer_Drevlianskyi_0405', camera_id='0405')
    assert dep.camera_id == '0405'


def test_history_unknown_defaults_false(make_ct_deployment):
    dep = make_ct_deployment()
    assert dep.history_unknown is False


def test_is_usable_null_treated_as_usable(make_ct_deployment):
    dep = make_ct_deployment()  # qc_data_not_usable не заданий → NULL
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
    """Generated-колонка існує й не ламає вставку (значення — Postgres-only)."""
    dep = make_ct_deployment(start_date=date(2025, 7, 1), end_date=date(2025, 9, 5))
    fetched = ct_session.query(Deployment).get(dep.id)
    assert hasattr(fetched, 'n_days_calc')  # колонка присутня в моделі/БД


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
    """Без дат деплоймент рахує всі згруповані фото локації."""
    loc = make_ct_location()
    dep = make_ct_deployment(location=loc, start_date=None, end_date=None)
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs)
    make_ct_photo(observation=obs)
    assert dep.count_photos(ct_session) == 2


def test_worst_case_on_overlap(ct_session, make_ct_location, make_ct_deployment):
    """Кадр, що потрапляє у два деплойменти, де один непридатний → виключити."""
    loc = make_ct_location()
    make_ct_deployment(location=loc, name='good',
                       start_date=date(2025, 7, 1), end_date=date(2025, 9, 10),
                       qc_data_not_usable=False)
    make_ct_deployment(location=loc, name='bad',
                       start_date=date(2025, 9, 5), end_date=date(2025, 12, 1),
                       qc_data_not_usable=True)

    capture = date(2025, 9, 7)  # у зоні перекриття
    overlapping = ct_session.query(Deployment).filter(
        Deployment.location_id == loc.id,
        Deployment.start_date <= capture,
        Deployment.end_date >= capture,
    ).all()
    assert len(overlapping) == 2

    # Правило «найгірший сценарій»: придатний лише якщо ЖОДЕН не виключає
    usable = all(d.is_usable() for d in overlapping)
    assert usable is False
