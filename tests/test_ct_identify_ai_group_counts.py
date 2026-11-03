"""
/identify AI filter: counts for "Будь-який", "Не пусто" and "Лише тварини".

Reported 03.11.2026: every species in the AI list had a count in brackets, the
three non-species options had none. They are now summed from the same query,
by the rules of observations_subq_for_ai_group (not empty = winning label is
not 'empty'; animals = also kingdom Animalia and not Homo sapiens). "Будь-який"
is every candidate series, with or without a prediction.

Run:
    venv/Scripts/python -m pytest tests/test_ct_identify_ai_group_counts.py -v
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATE = (Path(__file__).resolve().parents[1]
            / 'app' / 'camera_traps' / 'templates' / 'identification.html')


def _row(sp_id, sci, kingdom, count, cand_total=1000):
    r = MagicMock()
    r.id, r.scientific_name, r.kingdom = sp_id, sci, kingdom
    r.common_name_ua = r.common_name_en = f'name {sp_id}'
    r.pending_count, r.cand_total = count, cand_total
    return r


def _options(rows):
    from app.camera_traps.ai_runner import get_ai_filter_options
    sess = MagicMock()
    sess.query.return_value.first.return_value = MagicMock(id=1)
    sess.execute.return_value.fetchall.return_value = rows
    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        return get_ai_filter_options(user_id=1, user_inst_ids=[10], is_admin=False)


def test_group_counts_follow_the_group_filter_rules():
    opts = _options([
        _row(-1, 'empty', None, 500),
        _row(-3, 'vehicle', None, 20),
        _row(-4, 'Homo sapiens', 'Animalia', 7),
        _row(2, 'Capreolus capreolus', 'Animalia', 210),
        _row(3, 'Sus scrofa', 'Animalia', 140),
    ])
    assert opts['group_counts'] == {'': 1000, 'not_empty': 377, 'animals': 350}
    assert len(opts['items']) == 5


def test_no_predictions_gives_no_counts():
    assert _options([]) == {'items': [], 'group_counts': {}}


def test_species_list_wrapper_still_returns_items():
    from app.camera_traps.ai_runner import get_species_with_ai_predictions
    sess = MagicMock()
    sess.query.return_value.first.return_value = MagicMock(id=1)
    sess.execute.return_value.fetchall.return_value = [_row(2, 'Capreolus capreolus', 'Animalia', 3)]
    with patch('app.camera_traps.ai_runner.get_ct_session', return_value=sess):
        items = get_species_with_ai_predictions(user_id=1, is_admin=True)
    assert items == [{'id': 2, 'text': 'name 2 (Capreolus capreolus) (3)'}]


def test_page_shows_counts_for_group_options():
    html = TEMPLATE.read_text(encoding='utf-8')
    assert "group_count('not_empty')" in html and "group_count('animals')" in html
    assert 'data-suffix="{{ group_count(\'\') }}"' in html
    # The rebuild after a scope change keeps them, and so does the placeholder swap.
    assert "withCount(g.text, g.value)" in html
    assert "$opt.attr('data-suffix')" in html
