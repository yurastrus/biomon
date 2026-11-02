"""get_observation_ai_prediction(): a prediction written before its label got a
mapping (prediction_species_id NULL, e.g. DeepFaune "cow" in May 2026) still
gets a species from ai_label_map, so /identify shows "Корова" and pre-selects it
instead of the raw English label.

Session is mocked as in test_ai_quantity_max: query() gives the winning row and
the MAX, execute() answers the label-map and species lookups in that order.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _session(row, execute_results):
    q_win = MagicMock()
    for m in ('join', 'outerjoin', 'filter', 'order_by'):
        getattr(q_win, m).return_value = q_win
    q_win.first.return_value = row
    q_max = MagicMock()
    q_max.filter.return_value = q_max
    q_max.scalar.return_value = None
    sess = MagicMock()
    sess.query.side_effect = [q_win, q_max]
    sess.execute.side_effect = execute_results
    return sess


def _row(species_id, label):
    return SimpleNamespace(prediction_species_id=species_id, prediction_label=label,
                           prediction_score=0.8, animal_count=1, model_id=3)


def _result(scalar=None, fetchone=None):
    r = MagicMock()
    r.scalar.return_value = scalar
    r.fetchone.return_value = fetchone
    return r


COW = SimpleNamespace(common_name_ua='Корова', common_name_en='Cow', scientific_name='Bos taurus')


def _call(sess, lang='uk'):
    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        from app.camera_traps.ai_runner import get_observation_ai_prediction
        return get_observation_ai_prediction(1, lang_code=lang)


def test_null_species_resolved_through_label_map():
    sess = _session(_row(None, 'Cow '), [_result(scalar=-10), _result(fetchone=COW)])
    res = _call(sess)
    assert res['species_id'] == -10
    assert res['species_label'] == 'Корова'
    # the label is matched lower-cased and trimmed, as the map stores it
    assert sess.execute.call_args_list[0].args[1] == {'label': 'cow'}


def test_label_still_unmapped_keeps_raw_label():
    sess = _session(_row(None, 'undefined'), [_result(scalar=None)])
    res = _call(sess)
    assert res['species_id'] is None
    assert res['species_label'] == 'undefined'
    assert sess.execute.call_count == 1


def test_mapped_prediction_skips_label_map():
    sess = _session(_row(-10, 'cow'), [_result(fetchone=COW)])
    res = _call(sess, lang='en')
    assert res['species_id'] == -10
    assert res['species_label'] == 'Cow'
    assert sess.execute.call_count == 1
