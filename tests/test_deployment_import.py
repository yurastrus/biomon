"""
Тести імпорту деплойментів з Екселю (app/camera_traps/deployment_import.py).

Покривають:
  - коерсери (bool/int/year/camera_id, порожні/\xa0 -> None);
  - матчинг локацій за округленими координатами;
  - пропуск рядків без локації / без назви;
  - інверсну семантику (data_usable -> qc_data_not_usable);
  - ідемпотентність (повторний запуск оновлює, не дублює).
"""
from datetime import date

import pandas as pd
import pytest

from app.camera_traps.deployment_import import (
    import_deployments, coerce_bool, coerce_int, coerce_year, coerce_camera_id,
)
from app.camera_traps.models import Deployment


# ── коерсери ────────────────────────────────────────────────────────────────

def test_coerce_bool_variants():
    assert coerce_bool('TRUE') is True
    assert coerce_bool('no') is False
    assert coerce_bool(1) is True
    assert coerce_bool(0) is False
    assert coerce_bool(None) is None
    assert coerce_bool('\xa0') is None  # нерозривний пробіл -> NA
    with pytest.raises(ValueError):
        coerce_bool('maybe')


def test_coerce_int_and_year():
    assert coerce_int('75') == 75
    assert coerce_int(75.0) == 75
    assert coerce_int(None) is None
    assert coerce_year('2023-2024') == 2023   # зимова кампанія -> стартовий рік
    assert coerce_year(2025) == 2025


def test_coerce_camera_id_leading_zero_and_long():
    assert coerce_camera_id(405) == '0405'      # доповнення нулями до 4
    assert coerce_camera_id('12140') == '12140'  # 5-знач. валідний
    assert coerce_camera_id(None) is None


# ── імпорт ──────────────────────────────────────────────────────────────────

def _write_xlsx(tmp_path, rows):
    path = tmp_path / 'deployments.xlsx'
    pd.DataFrame(rows).to_excel(path, sheet_name='SMM_2025', index=False)
    return str(path)


def test_import_matches_location_and_skips_unmatched(tmp_path, ct_session, make_ct_location):
    loc = make_ct_location(latitude=48.5, longitude=24.5)
    xlsx = _write_xlsx(tmp_path, [
        {'deployment_id': 'D1', 'latitude': 48.5, 'longitude': 24.5,
         'study_year': 2025, 'study_season': 'Summer', 'camera_id': 405,
         'start_date': '2025-07-01', 'end_date': '2025-09-05',
         'qc_data_not_usable': False},
        {'deployment_id': 'D2', 'latitude': 10.0, 'longitude': 10.0,  # нема локації
         'study_year': 2025, 'qc_data_not_usable': True},
    ])

    report = import_deployments(ct_session, xlsx, sheets=['SMM_2025'])

    assert report['inserted'] == 1
    assert report['skipped_no_location'] == 1
    dep = ct_session.query(Deployment).filter_by(name='D1').one()
    assert dep.location_id == loc.id
    assert dep.camera_id == '0405'
    assert dep.start_date == date(2025, 7, 1)
    assert dep.qc_data_not_usable is False


def test_import_inverted_semantics(tmp_path, ct_session, make_ct_location):
    make_ct_location(latitude=48.5, longitude=24.5)
    # data_usable=TRUE означає придатні -> qc_data_not_usable=False
    xlsx = _write_xlsx(tmp_path, [
        {'deployment_id': 'D1', 'latitude': 48.5, 'longitude': 24.5, 'data_usable': True},
    ])
    import_deployments(ct_session, xlsx, sheets=['SMM_2025'])
    dep = ct_session.query(Deployment).filter_by(name='D1').one()
    assert dep.qc_data_not_usable is False


def test_import_skips_row_without_name(tmp_path, ct_session, make_ct_location):
    make_ct_location(latitude=48.5, longitude=24.5)
    xlsx = _write_xlsx(tmp_path, [
        {'deployment_id': None, 'latitude': 48.5, 'longitude': 24.5},
    ])
    report = import_deployments(ct_session, xlsx, sheets=['SMM_2025'])
    assert report['skipped_no_name'] == 1
    assert report['inserted'] == 0


def test_import_idempotent(tmp_path, ct_session, make_ct_location):
    make_ct_location(latitude=48.5, longitude=24.5)
    xlsx = _write_xlsx(tmp_path, [
        {'deployment_id': 'D1', 'latitude': 48.5, 'longitude': 24.5, 'n_photos': 100},
    ])
    r1 = import_deployments(ct_session, xlsx, sheets=['SMM_2025'])
    r2 = import_deployments(ct_session, xlsx, sheets=['SMM_2025'])
    assert r1['inserted'] == 1
    assert r2['inserted'] == 0
    assert r2['updated'] == 1
    assert ct_session.query(Deployment).filter_by(name='D1').count() == 1
