# SPDX-License-Identifier: AGPL-3.0-only
"""Group filters on the /identify AI select: "не пусто" and "лише тварини".

DeepFaune stores its non-taxonomic labels in `species` with negative ids next to
the real taxa. Crucially the negative range is mixed: `empty`, `vehicle`,
`motobike`, `quadbike` and `not identifiable` are not animals, but `cervid`,
`corvid`, `mouse` and `insect` are — they are coarse animal groups. So "animals
only" cannot be expressed as `id > 0`, which is the obvious and wrong rule.

The rule used instead is taxonomic: a non-animal label has no `kingdom`. That
survives a new model version adding labels, where a hard-coded id list would
silently misclassify whatever was added last. `Homo sapiens` is the one
deliberate exception — an animal taxonomically, not what a wildlife survey is
looking for.
"""
import pytest
from sqlalchemy.dialects import postgresql

from app.camera_traps.ai_runner import (
    AI_GROUP_ANIMALS,
    AI_GROUP_FILTERS,
    AI_GROUP_NOT_EMPTY,
    observations_subq_for_ai_filter,
    observations_subq_for_ai_group,
    observations_subq_for_ai_species,
)


def _sql(stmt):
    """Render under the PostgreSQL dialect — the one that actually runs.

    `distinct(col)` becomes `DISTINCT ON (...)`, which only exists in Postgres;
    compiling under the default dialect would render a plain `DISTINCT` and the
    assertions below would be checking something production never executes.
    """
    return ' '.join(str(stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={'literal_binds': True})).split())


# ── dispatch ─────────────────────────────────────────────────────────────────

def test_numeric_value_still_selects_one_species(app):
    """The parameter used to be an int and must keep working as one."""
    with app.app_context():
        sql = _sql(observations_subq_for_ai_filter('42'))
    assert 'species_id = 42' in sql


def test_int_and_string_forms_agree(app):
    with app.app_context():
        assert _sql(observations_subq_for_ai_filter(42)) == \
               _sql(observations_subq_for_ai_species(42))


@pytest.mark.parametrize('group', AI_GROUP_FILTERS)
def test_group_values_dispatch_to_the_group_query(app, group):
    with app.app_context():
        assert _sql(observations_subq_for_ai_filter(group)) == \
               _sql(observations_subq_for_ai_group(group))


@pytest.mark.parametrize('value', [None, '', 'garbage', 'animals; DROP TABLE', '1.5'])
def test_unparseable_value_means_no_filter(app, value):
    """An unrecognised filter must widen to "no filter", never narrow to nothing.

    Returning an empty subquery instead would leave the identification queue
    looking empty, which reads as "all done" — the most misleading failure this
    page could have.
    """
    with app.app_context():
        assert observations_subq_for_ai_filter(value) is None


def test_unknown_group_raises_rather_than_guessing(app):
    with app.app_context():
        with pytest.raises(ValueError, match='unknown AI group filter'):
            observations_subq_for_ai_group('mammals_only')


# ── what the SQL actually asks for ───────────────────────────────────────────

def test_not_empty_excludes_empty_and_nothing_else(app):
    with app.app_context():
        sql = _sql(observations_subq_for_ai_group(AI_GROUP_NOT_EMPTY))
    assert "scientific_name != 'empty'" in sql
    assert 'kingdom' not in sql, '"не пусто" must keep vehicles, people and the rest'
    assert 'Homo sapiens' not in sql


def test_animals_excludes_non_animals_by_taxonomy(app):
    with app.app_context():
        sql = _sql(observations_subq_for_ai_group(AI_GROUP_ANIMALS))
    assert "scientific_name != 'empty'" in sql
    assert "kingdom = 'Animalia'" in sql, 'the rule must be taxonomic, not an id list'
    assert "scientific_name != 'Homo sapiens'" in sql


def test_animals_rule_is_not_an_id_range(app):
    """Guard against the tempting `id > 0`: it would drop cervid, corvid, mouse
    and every other coarse animal group, which are exactly what AI most often
    returns."""
    with app.app_context():
        sql = _sql(observations_subq_for_ai_group(AI_GROUP_ANIMALS))
    assert 'species.id > 0' not in sql
    assert 'species_id > 0' not in sql


@pytest.mark.parametrize('group', AI_GROUP_FILTERS)
def test_group_queries_pick_one_winning_prediction_per_series(app, group):
    """A series can have predictions from several models; the filter must judge
    the winning one only, or a series counts under two different filters."""
    with app.app_context():
        sql = _sql(observations_subq_for_ai_group(group))
    assert 'DISTINCT ON' in sql.upper()
    assert 'accuracy_rank' in sql


@pytest.mark.parametrize('group', AI_GROUP_FILTERS)
def test_group_queries_select_only_observation_ids(app, group):
    """The result is used as an IN (...) subquery, so it must stay single-column."""
    with app.app_context():
        stmt = observations_subq_for_ai_group(group)
        assert len(stmt.selected_columns) == 1
        assert 'observation_id' in str(list(stmt.selected_columns)[0])


def test_species_filter_is_parameterised_not_interpolated(app):
    """Ordinary rendering must bind the id, not paste it into the SQL text."""
    with app.app_context():
        rendered = str(observations_subq_for_ai_species(7).compile(
            dialect=postgresql.dialect()))
    # The PostgreSQL dialect uses pyformat placeholders (%(name)s), not :name.
    assert '= 7' not in rendered, 'the id must be bound, not pasted into the SQL'
    assert '%(species_id_1)s' in rendered


# ── the template offers exactly what the backend understands ─────────────────

def test_template_offers_the_same_group_values(app):
    """A mismatch here is invisible: the select would send a value the backend
    silently ignores, and the filter would appear to do nothing."""
    import pathlib
    html = pathlib.Path(app.root_path, 'camera_traps', 'templates',
                        'identification.html').read_text(encoding='utf-8')
    for group in AI_GROUP_FILTERS:
        assert f'value="{group}"' in html, f'{group} missing from the select'
        assert f"value: '{group}'" in html, \
            f'{group} missing from the JS rebuild — a scope change would drop it'


def test_select_rebuild_preserves_a_chosen_group(app):
    """Changing the institution scope rebuilds the select from the species API;
    a group filter is not in that list and would be reset without this."""
    import pathlib
    html = pathlib.Path(app.root_path, 'camera_traps', 'templates',
                        'identification.html').read_text(encoding='utf-8')
    assert 'const isGroup = aiGroupOptions.some' in html
    assert 'isGroup ||' in html
