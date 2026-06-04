"""
Idea 5 (#21): дашборд калібрування AI (точність по видах).

Route /admin/ai/accuracy на основі ai_predictions.was_correct (Idea 4).
БД не потрібна — мокаємо ct-engine.

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_ai_calibration.py -v
"""
from unittest.mock import MagicMock, patch

URL = '/uk/camera-traps/admin/ai/accuracy'


def _engine_with_rows(rows):
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = False
    conn.execute.return_value.mappings.return_value.fetchall.return_value = rows
    return engine


def _row(**kw):
    base = {
        'species_id': 5, 'scientific_name': 'Capreolus capreolus',
        'common_name_ua': 'Козуля', 'common_name_en': 'Roe deer',
        'total': 10, 'correct': 8,
        'mean_score_correct': 0.9, 'mean_score_wrong': 0.4,
    }
    base.update(kw)
    return base


def test_renders_with_accuracy(auth_client):
    cl = auth_client(role='admin')
    with patch('app.camera_traps.database.get_ct_engine',
               return_value=_engine_with_rows([_row()])):
        resp = cl.get(URL)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Козуля' in html
    assert '80.0' in html          # accuracy 8/10
    assert '(8/10)' in html


def test_null_mean_score_renders_dash(auth_client):
    cl = auth_client(role='admin')
    row = _row(total=3, correct=3, mean_score_wrong=None)  # усі вгадані → wrong None
    with patch('app.camera_traps.database.get_ct_engine',
               return_value=_engine_with_rows([row])):
        resp = cl.get(URL)
    assert resp.status_code == 200
    assert '100.0' in resp.get_data(as_text=True)


def test_empty_state(auth_client):
    cl = auth_client(role='admin')
    with patch('app.camera_traps.database.get_ct_engine',
               return_value=_engine_with_rows([])):
        resp = cl.get(URL)
    assert resp.status_code == 200
    assert ('Поки немає даних' in resp.get_data(as_text=True)
            or 'No data' in resp.get_data(as_text=True))


def test_survives_db_error(auth_client):
    """Падіння БД не валить сторінку — показує порожній стан."""
    cl = auth_client(role='admin')
    with patch('app.camera_traps.database.get_ct_engine',
               side_effect=Exception('no db')):
        resp = cl.get(URL)
    assert resp.status_code == 200


def test_requires_admin(auth_client):
    cl = auth_client(role='ct_verifier')
    resp = cl.get(URL)
    assert resp.status_code in (302, 403)
