"""
#40: camera-trap landing page — the "Analytics" section comes FIRST
(aligned with the PAM hub: public analytics on top, role-based sections below).
"""
from unittest.mock import patch


def test_ct_overview_analytics_before_work(auth_client, db_session):
    """Admin sees both "Analytics" and "Work" — analytics must come first."""
    cl = auth_client(role='admin')
    resp = cl.get('/uk/camera-traps/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    i_analytics = body.find('>{}'.format('Аналітика'))
    if i_analytics == -1:
        i_analytics = body.find('Аналітика')
    i_work = body.find('Робота')
    assert i_analytics != -1, 'секція «Аналітика» не знайдена'
    assert i_work != -1, 'секція «Робота» не знайдена'
    assert i_analytics < i_work, 'секція «Аналітика» має бути вище за «Робота»'
