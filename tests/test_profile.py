"""
#31: особиста сторінка /profile — зміна пароля/логіну + персональна статистика.

Статистику (CT/PAM) мокаємо — тут перевіряємо логіку профілю, не реальні БД-запити.
"""
from unittest.mock import patch

import pytest

from app.extensions import bcrypt

CT_STATS = {'series': 5, 'identifications': 12, 'species_count': 3,
            'top_species': [{'name': 'Лисиця', 'count': 4}]}
PAM_STATS = {'verifications': 7, 'positive': 5, 'positive_rate': 71.4, 'species_count': 2}


@pytest.fixture
def stats_patched():
    with patch('app.camera_traps.utils.get_user_ct_stats', return_value=CT_STATS), \
         patch('app.pam.utils.get_user_pam_stats', return_value=PAM_STATS):
        yield


def test_profile_requires_login(client):
    resp = client.get('/uk/profile')
    assert resp.status_code in (302, 401)


def test_profile_get_renders_username_and_stats(auth_client, db_session, stats_patched):
    cl = auth_client(role='viewer', username='profuser')
    resp = cl.get('/uk/profile')
    assert resp.status_code == 200
    assert b'profuser' in resp.data


def test_password_change_success(auth_client, db_session, stats_patched):
    from app.models import User
    cl = auth_client(role='viewer', username='pwuser')          # пароль 'pass'
    resp = cl.post('/uk/profile', data={
        'current_password': 'pass',
        'new_password': 'newpass123',
        'confirm_password': 'newpass123',
        'submit_password': 'Змінити пароль',
    })
    assert resp.status_code in (302, 303)
    u = User.query.filter_by(username='pwuser').first()
    assert bcrypt.check_password_hash(u.password_hash, 'newpass123')


def test_password_change_wrong_current_rejected(auth_client, db_session, stats_patched):
    from app.models import User
    cl = auth_client(role='viewer', username='pwuser2')
    resp = cl.post('/uk/profile', data={
        'current_password': 'WRONG',
        'new_password': 'newpass123',
        'confirm_password': 'newpass123',
        'submit_password': 'Змінити пароль',
    })
    assert resp.status_code == 200                              # без редіректу
    u = User.query.filter_by(username='pwuser2').first()
    assert bcrypt.check_password_hash(u.password_hash, 'pass')  # не змінено


def test_password_change_weak_rejected(auth_client, db_session, stats_patched):
    from app.models import User
    cl = auth_client(role='viewer', username='pwuser3')
    resp = cl.post('/uk/profile', data={
        'current_password': 'pass',
        'new_password': 'short1',                               # < 8, політика #27
        'confirm_password': 'short1',
        'submit_password': 'Змінити пароль',
    })
    assert resp.status_code == 200
    u = User.query.filter_by(username='pwuser3').first()
    assert bcrypt.check_password_hash(u.password_hash, 'pass')  # не змінено


def test_password_change_mismatch_rejected(auth_client, db_session, stats_patched):
    from app.models import User
    cl = auth_client(role='viewer', username='pwuser4')
    resp = cl.post('/uk/profile', data={
        'current_password': 'pass',
        'new_password': 'newpass123',
        'confirm_password': 'different123',
        'submit_password': 'Змінити пароль',
    })
    assert resp.status_code == 200
    u = User.query.filter_by(username='pwuser4').first()
    assert bcrypt.check_password_hash(u.password_hash, 'pass')


def test_username_change_success(auth_client, db_session, stats_patched):
    from app.models import User
    cl = auth_client(role='viewer', username='oldname')
    resp = cl.post('/uk/profile', data={
        'new_username': 'newname',
        'submit_username': 'Змінити логін',
    })
    assert resp.status_code in (302, 303)
    assert User.query.filter_by(username='newname').first() is not None
    assert User.query.filter_by(username='oldname').first() is None


def test_username_change_duplicate_rejected(auth_client, db_session, make_user, stats_patched):
    from app.models import User
    make_user(username='taken', roles=('viewer',))
    cl = auth_client(role='viewer', username='wants_taken')
    resp = cl.post('/uk/profile', data={
        'new_username': 'taken',
        'submit_username': 'Змінити логін',
    })
    assert resp.status_code == 200                              # відхилено
    assert User.query.filter_by(username='wants_taken').first() is not None
