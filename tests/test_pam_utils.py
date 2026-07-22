"""
Tests for the pam module's helper functions.

We replace `get_pam_db_connection` with a mock -- the real PAM DB is untouched.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.pam.utils import (
    get_available_species,
    get_models_list,
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

    def test_model_with_id_binds_param(self):
        params = {'confidence': 0.5}
        assert _normalize_model_mode('model', 3, params) == 'model'
        assert params['model_id'] == 3

    def test_birdnet_does_not_bind_model_id(self):
        params = {'confidence': 0.5}
        _normalize_model_mode('birdnet', 3, params)
        assert 'model_id' not in params


class TestConfidenceFilterSql:
    def test_birdnet_is_unchanged_predicate(self):
        # Regression guard: default mode must be byte-identical to the old SQL.
        assert _confidence_filter_sql('birdnet') == 'd.confidence >= :confidence'

    def test_birdnet_respects_alias(self):
        assert _confidence_filter_sql('birdnet', alias='x') == 'x.confidence >= :confidence'

    def test_model_uses_detection_models_and_model_id(self):
        sql = _confidence_filter_sql('model', 7)
        assert 'detection_models' in sql
        assert ':model_id' in sql
        assert ':confidence' in sql
        assert 'EXISTS' in sql

    def test_combined_uses_any_model(self):
        sql = _confidence_filter_sql('combined')
        assert 'detection_models' in sql
        assert ':model_id' not in sql
        assert ':confidence' in sql

    def test_model_without_id_falls_back_to_birdnet(self):
        assert _confidence_filter_sql('model', None) == 'd.confidence >= :confidence'


class TestConfidenceValueSql:
    def test_birdnet_is_plain_column(self):
        assert _confidence_value_sql('birdnet') == 'd.confidence'

    def test_model_selects_that_models_confidence(self):
        sql = _confidence_value_sql('model', 2)
        assert 'detection_models' in sql and ':model_id' in sql

    def test_combined_selects_max(self):
        sql = _confidence_value_sql('combined')
        assert 'MAX(dm.confidence)' in sql


class TestGetModelsList:
    def _conn(self, rows):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        return conn

    def test_marks_reference_and_builds_label(self, app):
        from types import SimpleNamespace
        rows = [
            SimpleNamespace(model_id=1, name='BirdNET', version='2.4'),
            SimpleNamespace(model_id=2, name='Perch', version='v2'),
            SimpleNamespace(model_id=3, name='Nocmig', version=None),
        ]
        conn = self._conn(rows)
        with app.test_request_context('/'):
            with patch('app.pam.utils.get_pam_db_connection', return_value=conn):
                result = get_models_list()
        assert result[0] == {'model_id': 1, 'label': 'BirdNET 2.4', 'is_reference': True}
        assert result[1] == {'model_id': 2, 'label': 'Perch v2', 'is_reference': False}
        assert result[2]['label'] == 'Nocmig'  # version omitted when blank
        assert result[2]['is_reference'] is False

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
