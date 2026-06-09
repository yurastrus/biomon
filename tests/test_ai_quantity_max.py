"""
#35: get_observation_ai_prediction() повертає кількість особин для серії як
MAX(animal_count) у межах переможної моделі (а не з одного «переможного» фото).

AIPrediction використовує ARRAY/JSONB і відсутній у SQLite-фікстурі ct_session,
тож мокаємо сесію: перший query() — вибір переможного рядка, другий — MAX.
"""
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _winning_row(animal_count=1, model_id=7):
    return SimpleNamespace(
        prediction_species_id=42, prediction_label='roe deer',
        prediction_score=0.9, animal_count=animal_count, model_id=model_id,
    )


def _mock_session(win_row, max_count):
    q_win = MagicMock()
    for m in ('join', 'outerjoin', 'filter', 'order_by'):
        getattr(q_win, m).return_value = q_win
    q_win.first.return_value = win_row

    q_max = MagicMock()
    q_max.filter.return_value = q_max
    q_max.scalar.return_value = max_count

    sess = MagicMock()
    sess.query.side_effect = [q_win, q_max]
    return sess


def test_returns_max_animal_count_across_series():
    sess = _mock_session(_winning_row(animal_count=1), max_count=2)
    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        from app.camera_traps.ai_runner import get_observation_ai_prediction
        result = get_observation_ai_prediction(123)
    assert result['animal_count'] == 2      # фото [1,1,2] → 2
    assert result['species_id'] == 42       # вибір виду не змінився
    assert result['score'] == 0.9


def test_all_null_counts_fall_back_to_row():
    sess = _mock_session(_winning_row(animal_count=1), max_count=None)
    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        from app.camera_traps.ai_runner import get_observation_ai_prediction
        result = get_observation_ai_prediction(123)
    assert result['animal_count'] == 1      # усі NULL → поточна поведінка


def test_no_prediction_returns_none():
    sess = _mock_session(None, max_count=None)
    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        from app.camera_traps.ai_runner import get_observation_ai_prediction
        result = get_observation_ai_prediction(999)
    assert result is None
