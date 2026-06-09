"""
#36: CT export — режим 'location_timewindow' (інтервал незалежності).

ct_db — PostgreSQL (window-функції, EXTRACT EPOCH), несумісне зі SQLite-фікстурою,
тож мокаємо з'єднання й перевіряємо ПОБУДОВУ SQL + валідацію вікна:
  - режим будує CTE з is_new_event / EventGrouped;
  - вікно (хв) → :agg_seconds; clamp 1..EXPORT_MAX_AGG_MINUTES; невалідне → 5 хв.
Коректність на реальних даних перевірено окремо через тунель (read-only).
"""
from unittest.mock import patch, MagicMock


def _capture_export_sql(app, minutes):
    captured = []
    result = MagicMock()
    result.scalar.return_value = 0
    result.mappings.return_value.fetchall.return_value = []

    conn = MagicMock()
    def _exec(sql, params=None):
        captured.append((str(sql), dict(params or {})))
        return result
    conn.execute.side_effect = _exec

    engine = MagicMock()
    cm = engine.connect.return_value
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False

    with patch('app.camera_traps.data_export.get_ct_engine', return_value=engine), \
         app.app_context():
        from app.camera_traps.data_export import get_ct_occurrence_data
        get_ct_occurrence_data({
            'start_date': '2020-01-01', 'end_date': '2026-01-01',
            'aggregation': 'location_timewindow', 'aggregation_minutes': minutes,
        })
    return captured


def _agg_seconds(captured):
    return {p.get('agg_seconds') for _, p in captured if 'agg_seconds' in p}


def test_timewindow_builds_independence_interval_sql(app):
    captured = _capture_export_sql(app, 5)
    assert captured, 'жодного SQL не виконано'
    sql_all = ' '.join(s for s, _ in captured)
    assert 'is_new_event' in sql_all
    assert 'EventGrouped' in sql_all
    assert 300 in _agg_seconds(captured)        # 5 хв → 300 c


def test_timewindow_clamps_above_max(app):
    captured = _capture_export_sql(app, 999)     # > 60 → 3600 c
    assert _agg_seconds(captured) == {3600}


def test_timewindow_clamps_below_min(app):
    captured = _capture_export_sql(app, 0)        # < 1 → 60 c
    assert _agg_seconds(captured) == {60}


def test_timewindow_invalid_defaults_to_5(app):
    captured = _capture_export_sql(app, 'abc')    # невалідне → 5 хв → 300 c
    assert _agg_seconds(captured) == {300}
