"""
#40: стартова сторінка фотопасток — секція «Аналітика» йде ПЕРШОЮ
(узгоджено з PAM-хабом: публічна аналітика вгорі, рольові секції нижче).
"""
from unittest.mock import patch


def test_ct_overview_analytics_before_work(auth_client, db_session):
    """Адмін бачить і «Аналітика», і «Робота» — аналітика має бути вище."""
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
