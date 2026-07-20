"""
Tests for PAM analytics (app/pam/pam_evaluation_utils.py + route).

Structure:
  1. TestConvertNumpyTypes              — numpy → python conversion
  2. TestBuildInsufficientDataMessage   — building readable messages
  3. TestGetSpeciesDiagnostic           — SQL for species diagnostics
  4. TestGetSpeciesForDropdown          — SQL for the dropdown
  5. TestCalculateSpeciesMetrics        — Precision + bootstrap CI
  6. TestRecalculateAllMetrics          — orchestrator (various scenarios)
  7. TestRecalculateReturnContract      — return contract (reason, error)
  8. TestEvaluationResultsPage          — GET page
  9. TestAdminRecalculateRoute          — POST route and flash levels

Run:
    venv/Scripts/python -m pytest tests/test_pam_evaluation.py -v
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

import numpy as np


# ══════════════════════════════════════════════════════════════════════════════
# 1. convert_numpy_types — type conversion
# ══════════════════════════════════════════════════════════════════════════════

class TestConvertNumpyTypes(unittest.TestCase):

    def test_numpy_int_becomes_python_int(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        result = convert_numpy_types(np.int64(42))
        self.assertIsInstance(result, int)
        self.assertEqual(result, 42)

    def test_numpy_float_becomes_python_float(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        result = convert_numpy_types(np.float64(3.14))
        self.assertIsInstance(result, float)
        self.assertAlmostEqual(result, 3.14)

    def test_numpy_array_becomes_list(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        result = convert_numpy_types(np.array([1, 2, 3]))
        self.assertIsInstance(result, list)
        self.assertEqual(result, [1, 2, 3])

    def test_dict_with_numpy_values(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        result = convert_numpy_types({'a': np.int64(1), 'b': np.float64(2.5)})
        self.assertEqual(result, {'a': 1, 'b': 2.5})
        self.assertIsInstance(result['a'], int)
        self.assertIsInstance(result['b'], float)

    def test_nested_dict(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        result = convert_numpy_types({'outer': {'inner': np.int64(5)}})
        self.assertEqual(result, {'outer': {'inner': 5}})
        self.assertIsInstance(result['outer']['inner'], int)

    def test_list_with_numpy_values(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        result = convert_numpy_types([np.int64(1), np.float64(2.0)])
        self.assertEqual(result, [1, 2.0])

    def test_plain_python_types_unchanged(self):
        from app.pam.pam_evaluation_utils import convert_numpy_types
        self.assertEqual(convert_numpy_types('hello'), 'hello')
        self.assertEqual(convert_numpy_types(42), 42)
        self.assertIsNone(convert_numpy_types(None))


# ══════════════════════════════════════════════════════════════════════════════
# 2. _build_insufficient_data_message — insufficient-data messages
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildInsufficientDataMessage(unittest.TestCase):

    def _diag(self, **overrides):
        base = {
            'species_name':         'Test species',
            'total_segments':       10,
            'verified_segments':    5,
            'segments_meeting_min': 3,
            'total_verifications':  10,
            'min_verifications':    2,
            'required_segments':    5,
        }
        base.update(overrides)
        return base

    def test_zero_segments(self):
        from app.pam.pam_evaluation_utils import _build_insufficient_data_message
        diag = self._diag(total_segments=0, verified_segments=0,
                          segments_meeting_min=0, total_verifications=0)
        msg = _build_insufficient_data_message(diag)
        self.assertIn('немає жодного сегмента', msg)
        self.assertIn('Test species', msg)

    def test_segments_but_zero_verified(self):
        from app.pam.pam_evaluation_utils import _build_insufficient_data_message
        diag = self._diag(total_segments=8, verified_segments=0,
                          segments_meeting_min=0, total_verifications=0)
        msg = _build_insufficient_data_message(diag)
        self.assertIn('8 сегмент', msg)
        self.assertIn('жоден ще не верифіковано', msg)

    def test_below_threshold_mentions_counts(self):
        from app.pam.pam_evaluation_utils import _build_insufficient_data_message
        diag = self._diag(total_segments=20, verified_segments=7,
                          segments_meeting_min=3, total_verifications=14,
                          min_verifications=2, required_segments=5)
        msg = _build_insufficient_data_message(diag)
        self.assertIn('3 сегмент', msg)        # segments_meeting_min
        self.assertIn('≥2 верифікаціями', msg) # min_verifications
        self.assertIn('7 верифікованих', msg)  # verified_segments
        self.assertIn('14 верифікацій', msg)   # total_verifications
        self.assertIn('мінімум 5', msg)        # required_segments

    def test_message_suggests_action(self):
        from app.pam.pam_evaluation_utils import _build_insufficient_data_message
        diag = self._diag(segments_meeting_min=3)
        msg = _build_insufficient_data_message(diag)
        # Must give advice
        self.assertTrue(
            'додайте верифікацій' in msg or 'зменште поріг' in msg,
            f"Expected actionable hint in: {msg}"
        )

    def test_fallback_when_data_looks_ok(self):
        """Edge case: all counters pass, but we still hit the fallback."""
        from app.pam.pam_evaluation_utils import _build_insufficient_data_message
        diag = self._diag(segments_meeting_min=10, required_segments=5)
        msg = _build_insufficient_data_message(diag)
        self.assertIn('недостатньо даних', msg)


# ══════════════════════════════════════════════════════════════════════════════
# 3. _get_species_diagnostic — SQL query
# ══════════════════════════════════════════════════════════════════════════════

class TestGetSpeciesDiagnostic(unittest.TestCase):

    def _make_row(self, **vals):
        defaults = dict(
            scientific_name='Test sp',
            total_segments=10,
            verified_segments=5,
            total_verifications=12,
            segments_meeting_min=3,
        )
        defaults.update(vals)
        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
        return row

    def test_returns_structured_dict_with_all_keys(self):
        from app.pam.pam_evaluation_utils import _get_species_diagnostic
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = self._make_row()
        diag = _get_species_diagnostic(conn, species_id=42, min_verifications=2)
        expected = {'species_name', 'total_segments', 'verified_segments',
                    'segments_meeting_min', 'total_verifications',
                    'min_verifications', 'required_segments'}
        self.assertEqual(set(diag.keys()), expected)

    def test_required_segments_is_5(self):
        from app.pam.pam_evaluation_utils import _get_species_diagnostic
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = self._make_row()
        diag = _get_species_diagnostic(conn, species_id=42, min_verifications=2)
        self.assertEqual(diag['required_segments'], 5)

    def test_min_verifications_echoed_into_result(self):
        from app.pam.pam_evaluation_utils import _get_species_diagnostic
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = self._make_row()
        diag = _get_species_diagnostic(conn, species_id=42, min_verifications=3)
        self.assertEqual(diag['min_verifications'], 3)

    def test_null_counts_coerced_to_zero(self):
        from app.pam.pam_evaluation_utils import _get_species_diagnostic
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = self._make_row(
            total_segments=None, verified_segments=None,
            total_verifications=None, segments_meeting_min=None,
        )
        diag = _get_species_diagnostic(conn, species_id=42, min_verifications=2)
        self.assertEqual(diag['total_segments'], 0)
        self.assertEqual(diag['verified_segments'], 0)
        self.assertEqual(diag['total_verifications'], 0)
        self.assertEqual(diag['segments_meeting_min'], 0)

    def test_returns_none_when_species_not_found(self):
        from app.pam.pam_evaluation_utils import _get_species_diagnostic
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        diag = _get_species_diagnostic(conn, species_id=999, min_verifications=2)
        self.assertIsNone(diag)

    def test_params_passed_to_query(self):
        from app.pam.pam_evaluation_utils import _get_species_diagnostic
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = self._make_row()
        _get_species_diagnostic(conn, species_id=77, min_verifications=4)
        call_args = conn.execute.call_args
        params = call_args[0][1]
        self.assertEqual(params['sid'], 77)
        self.assertEqual(params['minv'], 4)


# ══════════════════════════════════════════════════════════════════════════════
# 4. get_species_for_dropdown — returns counters for the UI
# ══════════════════════════════════════════════════════════════════════════════

class TestGetSpeciesForDropdown(unittest.TestCase):

    def _mock_engine(self, rows):
        """Patch get_pam_db_connection to return a conn that yields `rows`."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        return conn

    def test_returns_rows_with_extended_counters(self):
        from app.pam.pam_evaluation_utils import get_species_for_dropdown
        row1 = MagicMock(species_id=1, scientific_name='A', common_name_uk='AA',
                         common_name_en='aa', verified_segments=10, total_verifications=25)
        row2 = MagicMock(species_id=2, scientific_name='B', common_name_uk='BB',
                         common_name_en='bb', verified_segments=2, total_verifications=4)
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._mock_engine([row1, row2])):
            result = get_species_for_dropdown()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].verified_segments, 10)
        self.assertEqual(result[1].total_verifications, 4)

    def test_returns_empty_list_on_exception(self):
        from app.pam.pam_evaluation_utils import get_species_for_dropdown
        conn = MagicMock()
        conn.execute.side_effect = Exception('DB down')
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = get_species_for_dropdown()
        self.assertEqual(result, [])

    def test_connection_closed_on_success(self):
        from app.pam.pam_evaluation_utils import get_species_for_dropdown
        conn = self._mock_engine([])
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn):
            get_species_for_dropdown()
        conn.close.assert_called_once()

    def test_connection_closed_on_exception(self):
        from app.pam.pam_evaluation_utils import get_species_for_dropdown
        conn = MagicMock()
        conn.execute.side_effect = Exception('DB error')
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            get_species_for_dropdown()
        conn.close.assert_called_once()

    def test_query_filters_to_verified_segments(self):
        """SQL must filter to only segments with a verification result."""
        from app.pam.pam_evaluation_utils import get_species_for_dropdown
        conn = self._mock_engine([])
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn):
            get_species_for_dropdown()
        sql = str(conn.execute.call_args[0][0])
        self.assertIn('verification_result IS NOT NULL', sql)


# ══════════════════════════════════════════════════════════════════════════════
# 5. calculate_species_metrics — the main calculation
# ══════════════════════════════════════════════════════════════════════════════

class TestCalculateSpeciesMetrics(unittest.TestCase):
    """
    Check edge conditions. Internal sklearn calls are mocked,
    so the test stays fast and deterministic.
    """

    def _conn_with_rows(self, rows):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        return conn

    def test_returns_none_for_under_5_segments(self):
        """Fewer than 5 segments → None (threshold from SQL HAVING)."""
        from app.pam.pam_evaluation_utils import calculate_species_metrics
        rows = [(i, 0.9, 1.0) for i in range(4)]  # 4 < 5
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._conn_with_rows(rows)):
            result = calculate_species_metrics(species_id=1, model_id=1)
        self.assertIsNone(result)

    def test_returns_none_when_no_consensus(self):
        """If no verification reached consensus_threshold → None."""
        from app.pam.pam_evaluation_utils import calculate_species_metrics
        # avg_verification = 0.5 — neither positive nor negative at threshold=2/3
        rows = [(i, 0.9, 0.5) for i in range(6)]
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._conn_with_rows(rows)):
            result = calculate_species_metrics(species_id=1, model_id=1, consensus_threshold=2/3)
        self.assertIsNone(result)

    def test_returns_dict_with_required_keys(self):
        from app.pam.pam_evaluation_utils import calculate_species_metrics
        # All 6 segments are positive (avg >= 2/3)
        rows = [(i, 0.9, 1.0) for i in range(6)]
        fake_logistic = {
            'beta0': 0.5, 'beta1': 1.0, 'r_squared': 0.8, 'n_samples': 6,
            'status': 'calculated',
            'p0_9_threshold': 0.9, 'p0_9_lower_ci': 0.85, 'p0_9_upper_ci': 0.95,
            'p0_95_threshold': 0.93, 'p0_95_lower_ci': None, 'p0_95_upper_ci': None,
            'p0_99_threshold': 0.97, 'p0_99_lower_ci': None, 'p0_99_upper_ci': None,
        }
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._conn_with_rows(rows)), \
             patch('app.pam.pam_evaluation_utils.calculate_logistic_regression',
                   return_value=fake_logistic), \
             patch('numpy.random.choice', return_value=np.array([1, 1, 1, 1, 1, 1])):
            result = calculate_species_metrics(species_id=1, model_id=1)

        self.assertIsNotNone(result)
        for key in ('species_id', 'precision_score', 'precision_lower_ci',
                    'precision_upper_ci', 'total_samples',
                    'logistic_beta0', 'logistic_status',
                    'p0_9_threshold', 'p0_95_threshold', 'p0_99_threshold'):
            self.assertIn(key, result)

    def test_precision_equals_one_when_all_correct(self):
        from app.pam.pam_evaluation_utils import calculate_species_metrics
        rows = [(i, 0.9, 1.0) for i in range(6)]
        fake_logistic = {k: None for k in ('beta0', 'beta1', 'r_squared',
            'n_samples', 'status', 'p0_9_threshold', 'p0_9_lower_ci',
            'p0_9_upper_ci', 'p0_95_threshold', 'p0_95_lower_ci', 'p0_95_upper_ci',
            'p0_99_threshold', 'p0_99_lower_ci', 'p0_99_upper_ci')}
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._conn_with_rows(rows)), \
             patch('app.pam.pam_evaluation_utils.calculate_logistic_regression',
                   return_value=fake_logistic), \
             patch('numpy.random.choice', return_value=np.array([1]*6)):
            result = calculate_species_metrics(species_id=1, model_id=1)
        self.assertEqual(result['precision_score'], 1.0)
        self.assertEqual(result['total_samples'], 6)

    def test_precision_equals_zero_when_all_wrong(self):
        from app.pam.pam_evaluation_utils import calculate_species_metrics
        # avg_verification = 0.0 → falls into "negative"
        rows = [(i, 0.9, 0.0) for i in range(6)]
        fake_logistic = {k: None for k in ('beta0', 'beta1', 'r_squared',
            'n_samples', 'status', 'p0_9_threshold', 'p0_9_lower_ci',
            'p0_9_upper_ci', 'p0_95_threshold', 'p0_95_lower_ci', 'p0_95_upper_ci',
            'p0_99_threshold', 'p0_99_lower_ci', 'p0_99_upper_ci')}
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._conn_with_rows(rows)), \
             patch('app.pam.pam_evaluation_utils.calculate_logistic_regression',
                   return_value=fake_logistic), \
             patch('numpy.random.choice', return_value=np.array([0]*6)):
            result = calculate_species_metrics(species_id=1, model_id=1)
        self.assertEqual(result['precision_score'], 0.0)

    def test_species_id_echoed_to_result(self):
        from app.pam.pam_evaluation_utils import calculate_species_metrics
        rows = [(i, 0.9, 1.0) for i in range(6)]
        fake_logistic = {k: None for k in ('beta0', 'beta1', 'r_squared',
            'n_samples', 'status', 'p0_9_threshold', 'p0_9_lower_ci',
            'p0_9_upper_ci', 'p0_95_threshold', 'p0_95_lower_ci', 'p0_95_upper_ci',
            'p0_99_threshold', 'p0_99_lower_ci', 'p0_99_upper_ci')}
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection',
                   return_value=self._conn_with_rows(rows)), \
             patch('app.pam.pam_evaluation_utils.calculate_logistic_regression',
                   return_value=fake_logistic), \
             patch('numpy.random.choice', return_value=np.array([1]*6)):
            result = calculate_species_metrics(species_id=123, model_id=7)
        self.assertEqual(result['species_id'], 123)
        self.assertEqual(result['model_id'], 7)


# ══════════════════════════════════════════════════════════════════════════════
# 6. recalculate_all_metrics — orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class TestRecalculateAllMetrics(unittest.TestCase):
    """
    Tests the full orchestrator cycle. The inner calculate_species_metrics
    is mocked — we care about the dispatch logic, not the SQL.
    """

    def _mock_conn(self, base_query_rows, diag_row=None):
        """Return a conn whose execute() yields base_query rows then diag row."""
        conn = MagicMock()
        results = []

        def execute_side_effect(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.fetchall.return_value = base_query_rows
            mock_result.fetchone.return_value = diag_row
            return mock_result

        conn.execute.side_effect = execute_side_effect
        return conn

    def _diag_row(self, **overrides):
        defaults = dict(
            scientific_name='Test sp',
            total_segments=10, verified_segments=5,
            total_verifications=12, segments_meeting_min=3,
        )
        defaults.update(overrides)
        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
        return row

    # ── Path A: no eligible species ──────────────────────────────────────────
    def test_no_eligible_species_all_mode(self):
        """If the database is empty and target_species_id=None → reason='no_eligible_species'."""
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = self._mock_conn(base_query_rows=[])
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, target_species_id=None)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'no_eligible_species')
        self.assertIn('error', result)
        self.assertNotIn('message', result)  # the old key is no longer used

    def test_no_data_for_specific_species_includes_diagnostic(self):
        """target_species_id with an empty result → reason='insufficient_data' + diagnostic."""
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        diag = self._diag_row(scientific_name='Bubo bubo',
                              verified_segments=2, total_verifications=3,
                              segments_meeting_min=2)
        conn = self._mock_conn(base_query_rows=[], diag_row=diag)
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, target_species_id=42)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'insufficient_data')
        self.assertIn('diagnostic', result)
        self.assertEqual(result['diagnostic']['species_name'], 'Bubo bubo')
        self.assertIn('Bubo bubo', result['error'])

    def test_species_not_found_fallback(self):
        """target_species_id that is not in the database at all → diagnostic=None."""
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = self._mock_conn(base_query_rows=[], diag_row=None)
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, target_species_id=999)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'insufficient_data')
        self.assertIn('не знайдено', result['error'].lower())

    # ── Path B: per-species mix ──────────────────────────────────────────────
    def test_successful_calculation_returns_success_true(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        # base_query returns 1 species
        base_result = MagicMock()
        base_result.fetchall.return_value = [(1, 10, 'Test sp')]
        conn.execute.return_value = base_result

        fake_metrics = {
            'species_id': 1, 'model_id': 10, 'precision_score': 0.9,
            'precision_lower_ci': 0.85, 'precision_upper_ci': 0.95,
            'total_samples': 10,
            'logistic_beta0': 0.5, 'logistic_beta1': 1.0,
            'logistic_r_squared': 0.8, 'logistic_n_samples': 10,
            'logistic_status': 'calculated',
            'p0_9_threshold': 0.9, 'p0_9_lower_ci': 0.85, 'p0_9_upper_ci': 0.95,
            'p0_95_threshold': 0.93, 'p0_95_lower_ci': None, 'p0_95_upper_ci': None,
            'p0_99_threshold': 0.97, 'p0_99_lower_ci': None, 'p0_99_upper_ci': None,
        }
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.calculate_species_metrics',
                   return_value=fake_metrics), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1)

        self.assertTrue(result['success'])
        self.assertEqual(result['calculated_count'], 1)
        self.assertEqual(result['failed_count'], 0)
        self.assertEqual(result['calculated_species'], ['Test sp'])

    def test_failed_species_gets_diagnostic_appended(self):
        """If calculate_species_metrics→None for a species, failed_species_detail
        must contain a record with numbers."""
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        diag = self._diag_row(scientific_name='Spinus spinus',
                              segments_meeting_min=3, verified_segments=7,
                              total_verifications=14)

        # 1st call — base_query (fetchall), the rest — for diag (fetchone)
        base_result = MagicMock()
        base_result.fetchall.return_value = [(5, 10, 'Spinus spinus')]
        diag_result = MagicMock()
        diag_result.fetchone.return_value = diag

        # Call sequence: base_query, UPDATE is_current=false, diag query
        # Simplified here — any execute() returns an object with both fetchall and fetchone
        combined = MagicMock()
        combined.fetchall.return_value = [(5, 10, 'Spinus spinus')]
        combined.fetchone.return_value = diag
        conn.execute.return_value = combined

        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.calculate_species_metrics', return_value=None), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, min_verifications=2)

        self.assertTrue(result['success'])
        self.assertEqual(result['failed_count'], 1)
        self.assertEqual(len(result['failed_species_detail']), 1)
        detail = result['failed_species_detail'][0]
        self.assertEqual(detail['name'], 'Spinus spinus')
        self.assertIn('Spinus spinus', detail['message'])
        self.assertIn('diagnostic', detail)

    # ── Path C: exception ────────────────────────────────────────────────────
    def test_exception_path_returns_reason_exception(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        conn.execute.side_effect = Exception('DB crashed')
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'exception')
        self.assertIn('DB crashed', result['error'])

    def test_exception_path_does_rollback(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        conn.execute.side_effect = Exception('boom')
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            recalculate_all_metrics(user_id=1)
        conn.rollback.assert_called_once()

    def test_connection_closed_in_all_paths(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        conn.execute.side_effect = Exception('boom')
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            recalculate_all_metrics(user_id=1)
        conn.close.assert_called_once()

    # ── mode marker ──────────────────────────────────────────────────────────
    def test_mode_single_when_target_species_given(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        result_mock = MagicMock()
        result_mock.fetchall.return_value = []
        result_mock.fetchone.return_value = None
        conn.execute.return_value = result_mock

        # When no eligible species, result has no 'mode' (it's success=False).
        # Let's test success path instead by providing data
        conn2 = MagicMock()
        combined = MagicMock()
        combined.fetchall.return_value = [(1, 10, 'X')]
        combined.fetchone.return_value = None
        conn2.execute.return_value = combined
        fake = {'species_id': 1, 'model_id': 10, 'precision_score': 0.5,
                'precision_lower_ci': 0.4, 'precision_upper_ci': 0.6,
                'total_samples': 5,
                'logistic_beta0': 0, 'logistic_beta1': 0, 'logistic_r_squared': 0,
                'logistic_n_samples': 5, 'logistic_status': 'calculated',
                'p0_9_threshold': 0.5, 'p0_9_lower_ci': 0, 'p0_9_upper_ci': 1,
                'p0_95_threshold': 0.5, 'p0_95_lower_ci': 0, 'p0_95_upper_ci': 1,
                'p0_99_threshold': 0.5, 'p0_99_lower_ci': 0, 'p0_99_upper_ci': 1}
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn2), \
             patch('app.pam.pam_evaluation_utils.calculate_species_metrics', return_value=fake), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, target_species_id=1)
        self.assertEqual(result['mode'], 'single')

    def test_mode_all_when_no_target_species(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        combined = MagicMock()
        combined.fetchall.return_value = [(1, 10, 'X')]
        combined.fetchone.return_value = None
        conn.execute.return_value = combined
        fake = {'species_id': 1, 'model_id': 10, 'precision_score': 0.5,
                'precision_lower_ci': 0.4, 'precision_upper_ci': 0.6,
                'total_samples': 5,
                'logistic_beta0': 0, 'logistic_beta1': 0, 'logistic_r_squared': 0,
                'logistic_n_samples': 5, 'logistic_status': 'calculated',
                'p0_9_threshold': 0.5, 'p0_9_lower_ci': 0, 'p0_9_upper_ci': 1,
                'p0_95_threshold': 0.5, 'p0_95_lower_ci': 0, 'p0_95_upper_ci': 1,
                'p0_99_threshold': 0.5, 'p0_99_lower_ci': 0, 'p0_99_upper_ci': 1}
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.calculate_species_metrics', return_value=fake), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, target_species_id=None)
        self.assertEqual(result['mode'], 'all')


# ══════════════════════════════════════════════════════════════════════════════
# 7. Return contract — guarantee stable keys for all scenarios
# ══════════════════════════════════════════════════════════════════════════════

class TestRecalculateReturnContract(unittest.TestCase):
    """
    Regression suite: make sure 'success': False STILL has 'error'
    (and not 'message') — the bug we just fixed.
    """

    def _conn_empty(self, diag_row=None):
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = diag_row
        conn.execute.return_value = result
        return conn

    def test_no_data_returns_error_key_not_message(self):
        """REGRESSION: it used to return {'message': ...} — the route read 'error'
        and showed 'Невідома помилка'."""
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = self._conn_empty()
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1)
        self.assertIn('error', result)
        self.assertNotIn('message', result)

    def test_insufficient_data_has_reason_field(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        diag = MagicMock(scientific_name='X', total_segments=0,
                         verified_segments=0, total_verifications=0,
                         segments_meeting_min=0)
        conn = self._conn_empty(diag)
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1, target_species_id=5)
        self.assertIn('reason', result)
        self.assertIn(result['reason'], {'insufficient_data', 'no_eligible_species', 'exception'})

    def test_exception_has_reason_exception(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        conn.execute.side_effect = Exception('xxx')
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1)
        self.assertEqual(result['reason'], 'exception')

    def test_success_path_has_calculated_and_failed_lists(self):
        from app.pam.pam_evaluation_utils import recalculate_all_metrics
        conn = MagicMock()
        combined = MagicMock()
        combined.fetchall.return_value = []
        combined.fetchone.return_value = None
        # Empty base list → no_eligible — switch to actually populated
        combined.fetchall.return_value = [(1, 10, 'X')]
        conn.execute.return_value = combined
        fake = {'species_id': 1, 'model_id': 10, 'precision_score': 0.5,
                'precision_lower_ci': 0.4, 'precision_upper_ci': 0.6, 'total_samples': 5,
                'logistic_beta0': 0, 'logistic_beta1': 0, 'logistic_r_squared': 0,
                'logistic_n_samples': 5, 'logistic_status': 'calculated',
                'p0_9_threshold': 0.5, 'p0_9_lower_ci': 0, 'p0_9_upper_ci': 1,
                'p0_95_threshold': 0.5, 'p0_95_lower_ci': 0, 'p0_95_upper_ci': 1,
                'p0_99_threshold': 0.5, 'p0_99_lower_ci': 0, 'p0_99_upper_ci': 1}
        with patch('app.pam.pam_evaluation_utils.get_pam_db_connection', return_value=conn), \
             patch('app.pam.pam_evaluation_utils.calculate_species_metrics', return_value=fake), \
             patch('app.pam.pam_evaluation_utils.current_app', new=MagicMock()):
            result = recalculate_all_metrics(user_id=1)
        # All these keys are part of the public contract
        for key in ('calculated_species', 'failed_species', 'failed_species_detail',
                    'calculated_count', 'failed_count', 'total_species_checked',
                    'logistic_regression_stats', 'mode'):
            self.assertIn(key, result)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers for route tests — Flask app setup
# ══════════════════════════════════════════════════════════════════════════════

class _PamEvalRouteBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock()
        )
        cls._ct_patcher.start()
        from app import create_app
        cls.app = create_app('testing')

    @classmethod
    def tearDownClass(cls):
        cls._ct_patcher.stop()
        os.environ.pop('DATABASE_URL', None)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app.extensions import db
        db.create_all()
        self._seed()
        self.client = self.app.test_client()

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self):
        from app.extensions import db, bcrypt
        from app.models import User, Role
        roles = {n: Role(name=n) for n in ('admin', 'manager', 'viewer')}
        db.session.add_all(roles.values())
        db.session.flush()
        pw = bcrypt.generate_password_hash('pass').decode()
        self.admin = User(username='eval_admin', password_hash=pw)
        self.admin.roles.append(roles['admin'])
        self.admin.roles.append(roles['manager'])
        self.manager = User(username='eval_manager', password_hash=pw)
        self.manager.roles.append(roles['manager'])
        self.viewer = User(username='eval_viewer', password_hash=pw)
        self.viewer.roles.append(roles['viewer'])
        db.session.add_all([self.admin, self.manager, self.viewer])
        db.session.commit()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True


# ══════════════════════════════════════════════════════════════════════════════
# 8. GET /<lang>/pam/evaluation/results
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluationResultsPage(_PamEvalRouteBase):

    def test_page_renders_with_species_list(self):
        with patch('app.pam.pam_evaluation_utils.get_evaluation_summary',
                   return_value={'summary': {'last_calculation': None}}), \
             patch('app.pam.pam_evaluation_utils.get_species_for_dropdown',
                   return_value=[]):
            resp = self.client.get('/uk/pam/evaluation/results')
        self.assertIn(resp.status_code, (200, 302))  # 302 if anonymous redirected

    def test_dropdown_data_passed_to_template(self):
        """Verify get_species_for_dropdown is called when rendering."""
        with patch('app.pam.pam_evaluation_utils.get_evaluation_summary',
                   return_value={'summary': {'last_calculation': None}}), \
             patch('app.pam.pam_evaluation_utils.get_species_for_dropdown',
                   return_value=[]) as mock_dropdown:
            self.client.get('/uk/pam/evaluation/results')
        mock_dropdown.assert_called()


# ══════════════════════════════════════════════════════════════════════════════
# 9. POST /<lang>/admin/evaluation/recalculate — flash levels
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminRecalculateRoute(_PamEvalRouteBase):

    def _post(self, **form_data):
        data = {'min_verifications': '2', 'species_choice': 'all', **form_data}
        return self.client.post('/uk/admin/evaluation/recalculate', data=data,
                                follow_redirects=False)

    def _get_flash(self):
        with self.client.session_transaction() as sess:
            return list(sess.get('_flashes', []))

    def test_anonymous_blocked(self):
        resp = self._post()
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_viewer_role_blocked(self):
        self._login(self.viewer.id)
        resp = self._post()
        self.assertIn(resp.status_code, (302, 401, 403))

    # ── success modes ────────────────────────────────────────────────────────
    def test_successful_recalculation_flash_success(self):
        self._login(self.manager.id)
        fake_result = {
            'success': True, 'mode': 'all',
            'calculated_count': 3, 'failed_count': 0,
            'calculated_species': ['A', 'B', 'C'], 'failed_species': [],
            'failed_species_detail': [],
            'total_species_checked': 3,
            'logistic_regression_stats': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post()
        flashes = self._get_flash()
        # success category present
        self.assertTrue(any(cat == 'success' for cat, _ in flashes),
                        f"Expected 'success' flash, got: {flashes}")

    def test_single_species_success_flash(self):
        self._login(self.manager.id)
        fake_result = {
            'success': True, 'mode': 'single',
            'calculated_count': 1, 'failed_count': 0,
            'calculated_species': ['Bubo bubo'], 'failed_species': [],
            'failed_species_detail': [],
            'total_species_checked': 1,
            'logistic_regression_stats': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post(species_choice='42')
        flashes = self._get_flash()
        self.assertTrue(any('1 виду' in msg or '1 вид' in msg
                            for _, msg in flashes))

    # ── degraded success: 0 calculated ───────────────────────────────────────
    def test_zero_calculated_flashes_warning_not_success(self):
        """Regression: when calculated=0 — do not say 'success', a warning is needed."""
        self._login(self.manager.id)
        fake_result = {
            'success': True, 'mode': 'single',
            'calculated_count': 0, 'failed_count': 1,
            'calculated_species': [], 'failed_species': ['X'],
            'failed_species_detail': [
                {'name': 'X', 'message': 'Mock msg', 'diagnostic': {}}
            ],
            'total_species_checked': 1,
            'logistic_regression_stats': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post(species_choice='42')
        flashes = self._get_flash()
        # No flash should be 'success'
        self.assertFalse(any(cat == 'success' for cat, _ in flashes),
                         f"Got unexpected success flash: {flashes}")
        self.assertTrue(any(cat == 'warning' for cat, _ in flashes),
                        f"Expected 'warning' flash, got: {flashes}")

    # ── insufficient_data path ───────────────────────────────────────────────
    def test_insufficient_data_uses_warning_not_danger(self):
        """REGRESSION: it used to show 'danger'+'Невідома помилка'.
        It must be 'warning' with a concrete description."""
        self._login(self.manager.id)
        fake_result = {
            'success': False,
            'reason': 'insufficient_data',
            'error': 'Spinus spinus: 3 сегмент(ів) з ≥2 верифікаціями. Потрібно мінімум 5.',
            'diagnostic': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post(species_choice='42')
        flashes = self._get_flash()
        self.assertFalse(any(cat == 'danger' for cat, _ in flashes),
                         f"Got unexpected danger flash: {flashes}")
        self.assertTrue(any(cat == 'warning' and 'Spinus spinus' in msg
                            for cat, msg in flashes),
                        f"Expected 'warning' with species name, got: {flashes}")

    def test_no_eligible_species_uses_warning(self):
        self._login(self.manager.id)
        fake_result = {
            'success': False,
            'reason': 'no_eligible_species',
            'error': 'У базі немає жодного виду з мінімум 5 сегментами.',
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post()
        flashes = self._get_flash()
        self.assertTrue(any(cat == 'warning' for cat, _ in flashes))

    # ── real exception ───────────────────────────────────────────────────────
    def test_exception_uses_danger(self):
        self._login(self.manager.id)
        fake_result = {
            'success': False,
            'reason': 'exception',
            'error': 'Connection refused',
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post()
        flashes = self._get_flash()
        self.assertTrue(any(cat == 'danger' for cat, _ in flashes),
                        f"Expected 'danger' flash for exception, got: {flashes}")

    # ── per-species detail messages ──────────────────────────────────────────
    def test_failed_species_detail_messages_flashed_individually(self):
        self._login(self.manager.id)
        fake_result = {
            'success': True, 'mode': 'all',
            'calculated_count': 1, 'failed_count': 2,
            'calculated_species': ['Good'],
            'failed_species': ['Bad1', 'Bad2'],
            'failed_species_detail': [
                {'name': 'Bad1', 'message': 'Bad1: not enough', 'diagnostic': {}},
                {'name': 'Bad2', 'message': 'Bad2: not enough', 'diagnostic': {}},
            ],
            'total_species_checked': 3,
            'logistic_regression_stats': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post()
        flashes = self._get_flash()
        msgs = [m for _, m in flashes]
        self.assertTrue(any('Bad1' in m for m in msgs))
        self.assertTrue(any('Bad2' in m for m in msgs))

    def test_too_many_failed_species_truncated(self):
        """More than 5 details → do not print them all, but 'та ще N'."""
        self._login(self.manager.id)
        details = [{'name': f'Sp{i}', 'message': f'Sp{i}: bad', 'diagnostic': {}}
                   for i in range(8)]
        fake_result = {
            'success': True, 'mode': 'all',
            'calculated_count': 1, 'failed_count': 8,
            'calculated_species': ['G'],
            'failed_species': [d['name'] for d in details],
            'failed_species_detail': details,
            'total_species_checked': 9,
            'logistic_regression_stats': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result):
            self._post()
        flashes = self._get_flash()
        msgs = [m for _, m in flashes]
        # Have a 'та ще N' (and N more) style message
        self.assertTrue(any('ще' in m and '3' in m for m in msgs),
                        f"Expected truncation flash 'та ще 3', got: {msgs}")

    # ── form validation ──────────────────────────────────────────────────────
    def test_invalid_min_verifications_warning(self):
        self._login(self.manager.id)
        # min_verifications=15 is outside the allowed 1-10 range
        self._post(min_verifications='15')
        flashes = self._get_flash()
        self.assertTrue(any(cat == 'warning' for cat, _ in flashes))

    def test_garbage_species_choice_treated_as_all(self):
        """If garbage came in species_choice — the parameter is None (i.e. 'all')."""
        self._login(self.manager.id)
        fake_result = {
            'success': True, 'mode': 'all',
            'calculated_count': 1, 'failed_count': 0,
            'calculated_species': ['X'], 'failed_species': [],
            'failed_species_detail': [],
            'total_species_checked': 1, 'logistic_regression_stats': {},
        }
        with patch('app.pam.pam_evaluation_utils.recalculate_all_metrics',
                   return_value=fake_result) as mock_calc:
            self._post(species_choice='not_a_number')
        # target_species_id must be None
        call_kwargs = mock_calc.call_args[1]
        self.assertIsNone(call_kwargs.get('target_species_id'))


if __name__ == '__main__':
    unittest.main()
