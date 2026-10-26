"""Order of photos inside a CT series.

EXIF time has whole-second resolution and burst cameras shoot 2-3 frames in
the same second. Ties used to fall to DB row order / photo id (= upload order,
random with the parallel /upload-fast pool), so on /identify an animal could
jump back. The camera's file counter now breaks ties.
"""
import inspect
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.camera_traps import fast_upload, utils
from app.camera_traps.utils import SERIES_PHOTO_ORDER_SQL, sort_series_photos

NEXT_URL = '/uk/camera-traps/api/next-observation-for-identification'
T = datetime(2025, 2, 22, 20, 54, 8)


def _p(id_, name, ts=T):
    return SimpleNamespace(id=id_, original_filename=name, captured_at=ts)


def test_ties_in_one_second_follow_the_file_counter():
    # Upload order (id) is the reverse of the shooting order.
    photos = [_p(3, 'IMG_0151.JPG'), _p(1, 'IMG_0153.JPG'), _p(2, 'IMG_0152.JPG')]
    assert [p.original_filename for p in sort_series_photos(photos)] == \
        ['IMG_0151.JPG', 'IMG_0152.JPG', 'IMG_0153.JPG']


def test_burst_suffixes_a_b_c():
    photos = [_p(1, 'I_00271c.JPG'), _p(2, 'I_00271a.JPG'), _p(3, 'I_00271b.JPG')]
    assert [p.original_filename for p in sort_series_photos(photos)] == \
        ['I_00271a.JPG', 'I_00271b.JPG', 'I_00271c.JPG']


def test_time_wins_over_file_name():
    """Counter rollover (IMG_9999 -> IMG_0001) must not reorder distinct seconds."""
    late = datetime(2025, 7, 18, 10, 54, 29)
    early = datetime(2025, 7, 18, 10, 54, 1)
    photos = [_p(1, 'IMG_0001.JPG', late), _p(2, 'IMG_9999.JPG', early)]
    assert [p.original_filename for p in sort_series_photos(photos)] == \
        ['IMG_9999.JPG', 'IMG_0001.JPG']


def test_same_name_and_second_falls_back_to_id():
    photos = [_p(5, 'IMG_0001.JPG'), _p(4, 'IMG_0001.JPG')]
    assert [p.id for p in sort_series_photos(photos)] == [4, 5]


def test_grouping_sql_uses_the_same_order():
    src = inspect.getsource(fast_upload.group_batch_into_series_sql)
    assert 'p.original_filename COLLATE "C"' in src
    assert 'ORDER BY captured_at, photo_id' not in src


def test_legacy_grouping_sorts_by_the_shared_key():
    assert 'sort_series_photos(' in inspect.getsource(utils.group_batch_into_series)


def test_renumber_script_matches_viewer_order():
    from scripts.renumber_series_photos import ORDER_SQL
    assert ORDER_SQL == SERIES_PHOTO_ORDER_SQL


@pytest.fixture
def ct(ct_session):
    from app.camera_traps import models as m
    m.identification_behaviors.create(bind=ct_session.get_bind(), checkfirst=True)
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def test_identify_payload_is_in_shooting_order(ct, auth_client, make_ct_location,
                                               make_ct_observation, make_ct_photo):
    loc = make_ct_location(name='Публічна', visibility_level=0)
    obs = make_ct_observation(location=loc)
    # Inserted (= uploaded) in the wrong order, all in the same second.
    for name in ('I_00271c.JPG', 'I_00271a.JPG', 'I_00271b.JPG'):
        make_ct_photo(observation=obs, captured_at=T, original_filename=name,
                      system_filename=f'sys_{name}')
    make_ct_photo(observation=obs, captured_at=datetime(2025, 2, 22, 20, 54, 14),
                  original_filename='I_00272a.JPG', system_filename='sys_I_00272a.JPG')
    ct.expire_all()

    cl = auth_client(role='ct_verifier')
    resp = cl.get(NEXT_URL, query_string={'start_obs_id': obs.id})

    assert resp.status_code == 200
    names = [p['debug_filename'] for p in resp.get_json()['photos']]
    assert names == ['sys_I_00271a.JPG', 'sys_I_00271b.JPG', 'sys_I_00271c.JPG',
                     'sys_I_00272a.JPG']
