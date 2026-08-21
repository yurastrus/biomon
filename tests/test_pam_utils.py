"""
Tests for the pam module's helper functions.

We replace `get_pam_db_connection` with a mock -- the real PAM DB is untouched.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.pam.utils import (
    get_available_species,
    get_models_list,
    get_model_conf_columns,
    _normalize_model_mode,
    _confidence_filter_sql,
    _confidence_value_sql,
    _verification_display_status,
)


def _make_mock_conn(rows):
    """Creates a fake connection where conn.execute(...).mappings().fetchall() -> rows."""
    conn = MagicMock()
    result = MagicMock()
    result.mappings.return_value.fetchall.return_value = rows
    conn.execute.return_value = result
    return conn


def test_get_available_species_returns_empty_on_db_error(app):
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError('boom')
    with app.test_request_context('/'):
        with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
            result = get_available_species('uk')
    assert result == []


def test_get_available_species_formats_uk_label(app):
    rows = [
        {'scientific_name': 'Bubo bubo', 'common_name_en': 'Eagle Owl',
         'common_name_uk': 'Пугач', 'required_role': None},
    ]
    conn = _make_mock_conn(rows)
    with app.test_request_context('/'):
        with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
            result = get_available_species('uk')
    assert len(result) == 1
    assert result[0]['value'] == 'Bubo bubo'
    assert 'Пугач' in result[0]['text']
    assert 'Bubo bubo' in result[0]['text']


def test_get_available_species_formats_en_label(app):
    rows = [
        {'scientific_name': 'Bubo bubo', 'common_name_en': 'Eagle Owl',
         'common_name_uk': 'Пугач', 'required_role': None},
    ]
    conn = _make_mock_conn(rows)
    with app.test_request_context('/'):
        with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
            result = get_available_species('en')
    assert 'Eagle Owl' in result[0]['text']


def test_get_available_species_sorted_alphabetically(app):
    rows = [
        {'scientific_name': 'Zelta zelta', 'common_name_en': 'Zeta',
         'common_name_uk': 'Зета', 'required_role': None},
        {'scientific_name': 'Alpha alpha', 'common_name_en': 'Alpha',
         'common_name_uk': 'Альфа', 'required_role': None},
    ]
    conn = _make_mock_conn(rows)
    with app.test_request_context('/'):
        with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
            result = get_available_species('en')
    assert result[0]['value'] == 'Alpha alpha'
    assert result[1]['value'] == 'Zelta zelta'


# ── Dashboard model switcher (Task B) ─────────────────────────────────────────

class TestNormalizeModelMode:
    def test_default_is_birdnet(self):
        assert _normalize_model_mode('birdnet', None) == 'birdnet'

    def test_invalid_mode_falls_back_to_birdnet(self):
        assert _normalize_model_mode('garbage', 5) == 'birdnet'

    def test_combined(self):
        assert _normalize_model_mode('combined', None) == 'combined'

    def test_model_without_id_falls_back(self):
        assert _normalize_model_mode('model', None) == 'birdnet'

    def test_model_with_known_column_is_model_mode(self):
        # Since migration 0006 the mode is valid only when the model HAS a score
        # column, and no :model_id is bound (a column name is interpolated).
        params = {'confidence': 0.5}
        with patch('app.pam.utils.get_model_conf_columns',
                   return_value={3: 'conf_perch_v2'}):
            assert _normalize_model_mode('model', 3, params) == 'model'
        assert 'model_id' not in params

    def test_model_without_conf_column_falls_back_to_birdnet(self):
        # A disabled model (Nocmig: conf_column IS NULL) cannot be filtered by,
        # so the switcher must degrade rather than return an empty result set.
        with patch('app.pam.utils.get_model_conf_columns',
                   return_value={1: 'confidence'}):
            assert _normalize_model_mode('model', 99) == 'birdnet'


class TestGetModelConfColumns:
    """models.conf_column is the ONLY source of the model -> column mapping
    (migration 0006); it is interpolated into SQL, so it is also a trust
    boundary."""

    def _conn(self, rows):
        from types import SimpleNamespace
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            SimpleNamespace(model_id=mid, conf_column=col) for mid, col in rows
        ]
        return conn

    def _call(self, app, conn):
        # refresh=True bypasses the process-lifetime cache so tests do not leak
        # into one another.
        with app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                return get_model_conf_columns(refresh=True)

    def test_maps_model_id_to_column(self, app):
        result = self._call(app, self._conn(
            [(1, 'confidence'), (2, 'conf_perch_v2')]))
        assert result == {1: 'confidence', 2: 'conf_perch_v2'}

    def test_query_excludes_disabled_models(self, app):
        conn = self._conn([])
        self._call(app, conn)
        assert 'conf_column IS NOT NULL' in str(conn.execute.call_args[0][0])

    def test_drops_column_names_that_are_not_safe_to_interpolate(self, app):
        # Defence in depth behind ck_models_conf_column: a value that somehow
        # got past the CHECK must never reach the SQL builders.
        result = self._call(app, self._conn([
            (1, 'confidence'),
            (2, 'conf; DROP TABLE detections--'),
            (3, 'Conf_Upper'),
            (4, None),
        ]))
        assert result == {1: 'confidence'}

    def test_returns_empty_on_db_error_with_a_cold_cache(self, app):
        # e.g. queried before migration 0006 has been applied: degrade to
        # reference-only behaviour instead of raising through the dashboards.
        import app.pam.utils as pam_utils
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError('column conf_column does not exist')
        with patch.object(pam_utils, '_conf_columns_cache', None), \
             app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                assert get_model_conf_columns(refresh=True) == {}

    def test_keeps_the_last_good_mapping_on_a_transient_db_error(self, app):
        import app.pam.utils as pam_utils
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError('connection reset')
        with patch.object(pam_utils, '_conf_columns_cache', {1: 'confidence'}), \
             app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                assert get_model_conf_columns(refresh=True) == {1: 'confidence'}


class TestConfidenceFilterSql:
    def test_birdnet_is_unchanged_predicate(self):
        # Regression guard: default mode must be byte-identical to the old SQL.
        assert _confidence_filter_sql('birdnet') == 'd.confidence >= :confidence'

    def test_birdnet_respects_alias(self):
        assert _confidence_filter_sql('birdnet', alias='x') == 'x.confidence >= :confidence'

    def test_model_uses_that_models_own_column(self):
        sql = _confidence_filter_sql('model', 7, columns={7: 'conf_perch_v2'})
        assert sql == 'd.conf_perch_v2 >= :confidence'
        assert 'detection_models' not in sql  # link table is gone

    def test_combined_ors_every_score_column(self):
        # OR rather than GREATEST(...) >= so each disjunct can still use its own
        # (species_id, <score>) index; GREATEST would not be indexable.
        sql = _confidence_filter_sql(
            'combined', columns={1: 'confidence', 2: 'conf_perch_v2'})
        assert sql == ('(d.conf_perch_v2 >= :confidence'
                       ' OR d.confidence >= :confidence)')

    def test_combined_with_one_model_is_a_plain_predicate(self):
        sql = _confidence_filter_sql('combined', columns={1: 'confidence'})
        assert sql == 'd.confidence >= :confidence'

    def test_model_without_id_falls_back_to_birdnet(self):
        assert _confidence_filter_sql('model', None) == 'd.confidence >= :confidence'

    def test_unknown_model_falls_back_to_reference_column(self):
        # Must never emit SQL naming an unmapped model, nor silently return zero
        # rows for a hand-crafted model_id.
        sql = _confidence_filter_sql('model', 404, columns={1: 'confidence'})
        assert sql == 'd.confidence >= :confidence'


class TestConfidenceValueSql:
    def test_birdnet_is_plain_column(self):
        assert _confidence_value_sql('birdnet') == 'd.confidence'

    def test_model_selects_that_models_column(self):
        sql = _confidence_value_sql('model', 2, columns={2: 'conf_perch_v2'})
        assert sql == 'd.conf_perch_v2'

    def test_combined_selects_greatest_across_columns(self):
        # GREATEST ignores NULLs and yields NULL only when every model is
        # silent - the same semantics the old MAX(dm.confidence) had.
        sql = _confidence_value_sql(
            'combined', columns={1: 'confidence', 2: 'conf_perch_v2'})
        assert sql == 'GREATEST(d.conf_perch_v2, d.confidence)'

    def test_combined_with_one_model_is_a_plain_column(self):
        assert _confidence_value_sql(
            'combined', columns={1: 'confidence'}) == 'd.confidence'


class TestGetModelsList:
    def _conn(self, rows):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        return conn

    def test_marks_reference_and_builds_label(self, app):
        from types import SimpleNamespace
        # The SQL filters out conf_column IS NULL models, so only importable /
        # filterable ones reach this code.
        rows = [
            SimpleNamespace(model_id=1, name='BirdNET', version='2.4',
                            conf_column='confidence'),
            SimpleNamespace(model_id=2, name='Perch', version='v2',
                            conf_column='conf_perch_v2'),
            SimpleNamespace(model_id=5, name='Nocmig', version=None,
                            conf_column='conf_nocmig'),
        ]
        conn = self._conn(rows)
        with app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                result = get_models_list()
        # is_reference comes from the column name, not a hardcoded model name.
        assert result[0] == {'model_id': 1, 'label': 'BirdNET 2.4',
                             'is_reference': True, 'conf_column': 'confidence'}
        assert result[1] == {'model_id': 2, 'label': 'Perch v2',
                             'is_reference': False, 'conf_column': 'conf_perch_v2'}
        assert result[2]['label'] == 'Nocmig'  # version omitted when blank
        assert result[2]['is_reference'] is False

    def test_query_excludes_models_without_a_score_column(self, app):
        conn = self._conn([])
        with app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                get_models_list()
        assert 'conf_column IS NOT NULL' in str(conn.execute.call_args[0][0])

    def test_returns_empty_on_db_error(self, app):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError('no models table')
        with app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                assert get_models_list() == []


# ---------------------------------------------------------------------------
# _verification_display_status — chart colour status derived from segment votes.
# Mirrors the 2/3 consensus rule in migration 0004 but also surfaces single votes.
# ---------------------------------------------------------------------------

# Signature: _verification_display_status(consensus_result, total_votes, positive_votes)
@pytest.mark.parametrize("consensus, total, positive, expected", [
    # dvm authoritative result wins regardless of live counts.
    (1, 1, 1, 'consensus_confirmed'),   # legacy hand-verified single vote -> dark
    (0, 1, 0, 'consensus_rejected'),    # legacy authoritative rejection -> dark
    (1, 0, 0, 'consensus_confirmed'),   # dvm set even with no live counts
    # dvm NULL: derive from live votes.
    (None, 0, 0, 'unverified'),
    (None, None, None, 'unverified'),
    # Exactly one live vote, no consensus recorded -> single_* (light, visible).
    (None, 1, 1, 'single_confirmed'),
    (None, 1, 0, 'single_rejected'),
    # Two live votes, dvm not yet upserted: unanimous -> consensus; split -> blue.
    (None, 2, 2, 'consensus_confirmed'),
    (None, 2, 0, 'consensus_rejected'),
    (None, 2, 1, 'unverified'),
    # 2/3 threshold boundary both directions (dvm NULL, stale-fix path).
    (None, 3, 2, 'consensus_confirmed'),   # 2/3 >= threshold
    (None, 3, 1, 'consensus_rejected'),    # 1/3 <= (1 - threshold)
    (None, 3, 3, 'consensus_confirmed'),
    (None, 3, 0, 'consensus_rejected'),
])
def test_verification_display_status(consensus, total, positive, expected):
    assert _verification_display_status(consensus, total, positive) == expected


def test_verification_display_status_single_positive_not_hidden():
    """The reported bug: one in-app verification must not read as 'unverified'."""
    assert _verification_display_status(None, 1, 1) == 'single_confirmed'
    assert _verification_display_status(None, 1, 0) == 'single_rejected'


def test_verification_display_status_legacy_authoritative_stays_dark():
    """Legacy hand-verified single-vote segments (dvm set) render as consensus (dark)."""
    assert _verification_display_status(1, 1, 1) == 'consensus_confirmed'
    assert _verification_display_status(0, 1, 0) == 'consensus_rejected'
