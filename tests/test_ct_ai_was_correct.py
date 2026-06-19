"""
Idea 4 (#20): record the correctness of the AI prediction at consensus time.

mark_observation_complete(winner_species_id) sets was_correct on each
AIPrediction of the series: True/False by species match, None if AI didn't
identify a species. Without winner_species_id (the old call) AI predictions
are left untouched.

ai_predictions uses JSONB (not created on SQLite) - we test the logic via
a mock session, without a real DB.

Run:
    venv/Scripts/python -m pytest tests/test_ct_ai_was_correct.py -v
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.camera_traps.utils import mark_observation_complete


def _session_with_preds(obs, preds):
    """Mock db_session: .query(Observation).get() -> obs;
    .query(AIPrediction).filter().all() -> preds."""
    sess = MagicMock()
    q = sess.query.return_value
    q.get.return_value = obs
    q.filter.return_value.all.return_value = preds
    return sess


def test_model_has_was_correct_column():
    from app.camera_traps.models import AIPrediction
    assert 'was_correct' in AIPrediction.__table__.columns.keys()


def test_records_correctness_on_consensus(app):
    obs = SimpleNamespace(status='pending', photos=[])
    correct = SimpleNamespace(prediction_species_id=5, was_correct=None)
    wrong = SimpleNamespace(prediction_species_id=9, was_correct=None)
    no_species = SimpleNamespace(prediction_species_id=None, was_correct=None)
    sess = _session_with_preds(obs, [correct, wrong, no_species])

    with app.app_context():
        mark_observation_complete(1, db_session=sess, winner_species_id=5)

    assert obs.status == 'completed'
    assert correct.was_correct is True
    assert wrong.was_correct is False
    assert no_species.was_correct is None   # AI didn't identify a species -> undetermined


def test_no_winner_leaves_predictions_untouched(app):
    """The old call without winner_species_id leaves AI predictions untouched."""
    obs = SimpleNamespace(status='pending', photos=[])
    pred = SimpleNamespace(prediction_species_id=5, was_correct=None)
    sess = _session_with_preds(obs, [pred])

    with app.app_context():
        mark_observation_complete(1, db_session=sess)  # no winner

    assert obs.status == 'completed'
    assert pred.was_correct is None       # untouched
    # AIPrediction wasn't even queried (filter not called for preds)


def test_photos_marked_completed(app):
    p1 = SimpleNamespace(status='pending')
    p2 = SimpleNamespace(status='pending')
    obs = SimpleNamespace(status='pending', photos=[p1, p2])
    sess = _session_with_preds(obs, [])

    with app.app_context():
        mark_observation_complete(1, db_session=sess, winner_species_id=5)

    assert obs.status == 'completed'
    assert p1.status == 'completed'
    assert p2.status == 'completed'
