"""
One rule for the series-level AI answer (2026-11-19), shared by the /identify
hint, the AI filter and its counts, and the export of AI-only series.

Reported: series 132583 sat under "Ведмідь бурий" in the AI filter while the
hint said "AI пропонує: Пусто, 100%". 14 of its 27 frames were bear (0.99),
13 empty (1.0). The hint took the single highest-scoring frame; the filter
took an arbitrary frame of the best model. On prod 1393 series with an animal
got an "Empty" hint (955 still pending).

Rule: within the most accurate model, 'empty' only if every frame is empty;
otherwise the species on the most frames, ties by best score.
"""
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.camera_traps.ai_runner import (
    AI_EMPTY_SPECIES_ID,
    SERIES_PICK_ORDER_SQL,
    _winning_prediction_subq,
    pick_series_prediction,
)

BEAR, DEER = 36, 3
EMPTY = AI_EMPTY_SPECIES_ID


def _pred(species_id, score, model_id=5, label=None):
    return SimpleNamespace(prediction_species_id=species_id, prediction_score=score,
                           model_id=model_id, prediction_label=label, animal_count=1)


def _frames(*spec, model_id=5, rank=0):
    """spec: (species_id, score, how_many) triples."""
    return [(_pred(sp, sc, model_id), rank) for sp, sc, n in spec for _ in range(n)]


def test_series_132583_bear_beats_higher_scored_empty_frames():
    rows = _frames((EMPTY, 1.0, 13), (BEAR, 0.99, 14))
    assert pick_series_prediction(rows).prediction_species_id == BEAR


def test_a_single_animal_frame_is_enough_to_not_be_empty():
    rows = _frames((EMPTY, 1.0, 26), (BEAR, 0.6, 1))
    assert pick_series_prediction(rows).prediction_species_id == BEAR


def test_all_empty_frames_give_empty():
    rows = _frames((EMPTY, 1.0, 5))
    assert pick_series_prediction(rows).prediction_species_id == EMPTY


def test_species_on_more_frames_wins_over_higher_score():
    rows = _frames((DEER, 0.95, 2), (BEAR, 0.7, 5))
    assert pick_series_prediction(rows).prediction_species_id == BEAR


def test_tie_on_frames_goes_to_best_score():
    rows = _frames((DEER, 0.95, 3), (BEAR, 0.7, 3))
    assert pick_series_prediction(rows).prediction_species_id == DEER


def test_more_accurate_model_still_decides_first():
    rows = (_frames((BEAR, 0.99, 10), model_id=5, rank=1)
            + _frames((DEER, 0.6, 1), model_id=9, rank=3))
    picked = pick_series_prediction(rows)
    assert picked.prediction_species_id == DEER and picked.model_id == 9


def test_returns_the_best_frame_of_the_chosen_species():
    rows = [(_pred(BEAR, 0.5), 0), (_pred(BEAR, 0.97), 0), (_pred(EMPTY, 1.0), 0)]
    assert pick_series_prediction(rows).prediction_score == 0.97


def test_no_rows_gives_none():
    assert pick_series_prediction([]) is None


def _sql(stmt):
    return ' '.join(str(stmt.compile(dialect=postgresql.dialect(),
                                     compile_kwargs={'literal_binds': True})).split())


def test_filter_subquery_counts_frames_before_picking(app):
    """The filter must aggregate per species before DISTINCT ON, and order
    'empty' last, so it cannot disagree with the hint."""
    with app.app_context():
        sql = _sql(_winning_prediction_subq())
    assert 'count(*) AS n_frames' in sql
    assert 'GROUP BY' in sql
    assert f'coalesce(anon_1.species_id, 0) = {EMPTY}' in sql
    assert sql.index('n_frames DESC') > sql.index('accuracy_rank')


def test_sql_order_matches_python_rule():
    flat = ' '.join(SERIES_PICK_ORDER_SQL.split())
    assert flat.index('accuracy_rank') < flat.index(f'= {EMPTY})') \
        < flat.index('n_frames DESC') < flat.index('best_score DESC')
