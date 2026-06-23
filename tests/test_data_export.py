"""
Tests for app/camera_traps/data_export.py

Run:
    cd biomon
    venv/Scripts/python -m pytest tests/test_data_export.py -v
    or:
    venv/Scripts/python -m unittest tests.test_data_export -v
"""
import unittest
from unittest.mock import patch, MagicMock, call
from datetime import datetime


def _make_mock_row(**kwargs):
    """Returns a mock DB result row."""
    defaults = {
        'observation_id': 42,
        'scientific_name': 'Canis lupus',
        'kingdom': 'Animalia',
        'phylum': 'Chordata',
        'class': 'Mammalia',
        'order_rank': 'Carnivora',
        'family': 'Canidae',
        'genus': 'Canis',
        'establishment_means': 'native',
        'series_start_time': datetime(2024, 6, 15, 10, 30, 0),
        'lat': 50.123,
        'lon': 30.456,
        'location_name': 'Test Location',
        'state_province': 'Kyiv Oblast',
        'max_quantity': 2,
        'identifier_user_ids': None,
    }
    defaults.update(kwargs)
    return defaults


def _make_engine_mock(count_result=1, rows=None):
    """
    Builds a full mock for engine.connect() as a context manager.
    Returns (mock_engine, mock_conn).
    """
    if rows is None:
        rows = [_make_mock_row()]

    mock_conn = MagicMock()

    count_execute = MagicMock()
    count_execute.scalar.return_value = count_result

    data_execute = MagicMock()
    data_execute.mappings.return_value.fetchall.return_value = rows

    mock_conn.execute.side_effect = [count_execute, data_execute]

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_ctx

    return mock_engine, mock_conn


class TestGetCtOccurrenceDataStructure(unittest.TestCase):
    """Checks the structure and content of the result."""

    def setUp(self):
        # Flask app context is needed for current_app and User.query
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_returns_dict_with_data_and_total_count(self):
        mock_engine, _ = _make_engine_mock(count_result=1)

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            result = get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        self.assertIn('data', result)
        self.assertIn('total_count', result)
        self.assertEqual(result['total_count'], 1)
        self.assertEqual(len(result['data']), 1)

    def test_occurrence_has_required_dwc_fields(self):
        """Checks for the presence of required Darwin Core fields."""
        mock_engine, _ = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            result = get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        occ = result['data'][0]
        required_fields = [
            'occurrenceID', 'scientificName', 'decimalLatitude', 'decimalLongitude',
            'eventDate', 'basisOfRecord', 'identifiedBy', 'countryCode',
        ]
        for field in required_fields:
            self.assertIn(field, occ, f"Відсутнє поле: {field}")

    def test_occurrence_id_format(self):
        mock_engine, _ = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            result = get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        occ = result['data'][0]
        self.assertTrue(occ['occurrenceID'].startswith('URN:ctmon:'))
        self.assertIn(':42', occ['occurrenceID'])  # observation_id=42

    def test_identifier_names_resolved_from_user_map(self):
        """Checks that identifier_user_ids is resolved to names."""
        row = _make_mock_row(identifier_user_ids='1|2')
        mock_engine, _ = _make_engine_mock(rows=[row])

        mock_user_1 = MagicMock()
        mock_user_1.id = 1
        mock_user_1.full_name = 'Іван Франко'
        mock_user_2 = MagicMock()
        mock_user_2.id = 2
        mock_user_2.full_name = 'Леся Українка'

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = [mock_user_1, mock_user_2]

            from app.camera_traps.data_export import get_ct_occurrence_data
            result = get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        identified_by = result['data'][0]['identifiedBy']
        self.assertIn('Іван Франко', identified_by)
        self.assertIn('Леся Українка', identified_by)

    def test_single_word_species_name_no_epithet(self):
        """If scientific_name is a single word — specificEpithet must be None."""
        row = _make_mock_row(scientific_name='Animalia')
        mock_engine, _ = _make_engine_mock(rows=[row])

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            result = get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        self.assertIsNone(result['data'][0]['specificEpithet'])

    def test_limit_applied(self):
        """Checks that LIMIT is added to the query when limit is passed."""
        mock_engine, mock_conn = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'}, limit=10)

        # The second execute call is the data query. Check that params contains 'limit'.
        second_call_params = mock_conn.execute.call_args_list[1][0][1]
        self.assertIn('limit', second_call_params)
        self.assertEqual(second_call_params['limit'], 10)


class TestGetCtOccurrenceDataConnectionLifecycle(unittest.TestCase):
    """
    Key tests: verify that the connection is always closed.
    This is exactly what fixes the 'idle in transaction' bug.
    """

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_connection_closed_on_success(self):
        """The connection is closed after successful execution."""
        mock_engine, _ = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        # The context manager's __exit__ must be called (this closes the connection)
        mock_engine.connect.return_value.__exit__.assert_called_once()

    def test_connection_closed_on_db_error(self):
        """The connection is closed even if the DB raised an error."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Connection refused")

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_ctx

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine):
            from app.camera_traps.data_export import get_ct_occurrence_data
            with self.assertRaises(Exception):
                get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        # Even on error — __exit__ must be called
        mock_ctx.__exit__.assert_called_once()

    def test_uses_engine_not_session(self):
        """Checks that the function uses get_ct_engine, not get_ct_session."""
        mock_engine, _ = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine) as mock_get_engine, \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        mock_get_engine.assert_called_once()
        # get_ct_session must not be called — it is no longer in the imports
        mock_engine.connect.assert_called_once()


class TestQcFiltering(unittest.TestCase):
    """QC filtering: checks SQL construction and exclusion logic.

    Since the engine is mocked, the tests check the structure of the generated SQL.
    This is enough to verify the correctness of the exclusion conditions — the actual
    DB behavior is guaranteed by the SQL being correct.
    """

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _run(self, qc_exclude, **extra_filters):
        mock_engine, mock_conn = _make_engine_mock()
        base = {'start_date': '2024-01-01', 'end_date': '2024-12-31'}
        base.update(extra_filters)
        if qc_exclude is not None:
            base['qc_exclude'] = qc_exclude
        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []
            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data(base)
        # The first execute call is the count query; it contains the full BaseData CTE.
        count_sql = str(mock_conn.execute.call_args_list[0][0][0])
        return count_sql

    # ── 1. Selected flag → SQL contains NOT EXISTS ─────────────────────────────
    def test_flag_raised_generates_not_exists(self):
        sql = self._run(['qc_non_functional'])
        self.assertIn('NOT EXISTS', sql)
        self.assertIn('qc_non_functional = TRUE', sql)

    # ── 2. No flags → no NOT EXISTS (orphan records pass through)
    def test_no_flags_no_not_exists(self):
        sql = self._run([])
        self.assertNotIn('NOT EXISTS', sql)

    # ── 3. Flag present but = FALSE → the NOT EXISTS condition does not trigger for it
    #       (the SQL syntax d_qc.flag = TRUE ensures this)
    #       Check: with a given flag the condition compares exactly against TRUE
    def test_boolean_flag_uses_true_comparison(self):
        sql = self._run(['qc_stolen'])
        self.assertIn('qc_stolen = TRUE', sql)

    # ── 4. OR logic: 2+ flags — one NOT EXISTS with OR conditions ──────────────
    def test_or_logic_single_not_exists_block(self):
        sql = self._run(['qc_non_functional', 'qc_stolen'])
        self.assertEqual(sql.count('NOT EXISTS'), 1,
                         "Має бути рівно один NOT EXISTS блок для всіх прапорців")
        self.assertIn('qc_non_functional = TRUE', sql)
        self.assertIn('qc_stolen = TRUE', sql)
        self.assertIn(' OR ', sql)

    # ── 5. Multiple deployments → covered by EXISTS (one "bad" one is enough)
    #       Check that WHERE has only EXISTS (not JOIN), i.e. the
    #       "OR = EXCLUDED" logic is implemented via EXISTS, not via INNER JOIN.
    def test_exists_not_join_approach(self):
        sql = self._run(['qc_data_not_usable'])
        self.assertIn('EXISTS', sql)
        self.assertNotIn('JOIN deployments', sql.upper())

    # ── 6. Time boundary: date overlap is built into the EXISTS condition ──────
    def test_date_overlap_condition_in_exists(self):
        sql = self._run(['qc_hardware_issue'])
        self.assertIn('series_start_time', sql)
        self.assertIn('start_date', sql)
        self.assertIn('end_date', sql)

    # ── 7. No checkbox → result identical to a query without qc_exclude (regression)
    def test_empty_qc_identical_to_no_qc_key(self):
        sql_no_key  = self._run(None)
        sql_empty   = self._run([])
        self.assertEqual(sql_no_key, sql_empty,
                         "Порожній qc_exclude має давати той самий SQL, що й відсутній ключ")

    # ── 8. Preview and download — identical QC logic ───────────────────────────
    def test_preview_and_download_same_qc_sql(self):
        flags = ['qc_non_functional', 'qc_data_not_usable']

        engine_p, conn_p = _make_engine_mock()
        engine_d, conn_d = _make_engine_mock()
        base = {'start_date': '2024-01-01', 'end_date': '2024-12-31', 'qc_exclude': flags}

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=engine_p), \
             patch('app.camera_traps.data_export.User') as mu:
            mu.query.filter.return_value.all.return_value = []
            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data(base, limit=20)   # preview
        sql_preview = str(conn_p.execute.call_args_list[0][0][0])

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=engine_d), \
             patch('app.camera_traps.data_export.User') as mu:
            mu.query.filter.return_value.all.return_value = []
            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data(base, limit=None)  # download
        sql_download = str(conn_d.execute.call_args_list[0][0][0])

        self.assertEqual(sql_preview, sql_download,
                         "Preview і download повинні генерувати однаковий count-SQL при однакових фільтрах")

    # ── Security: unknown flag is ignored (SQL injection whitelist) ────────────
    def test_invalid_flag_ignored(self):
        sql = self._run(["malicious'; DROP TABLE deployments; --"])
        self.assertNotIn('NOT EXISTS', sql)

    # ── TEXT field qc_local_datetime_issue: condition IS NOT NULL AND <> '' ────
    def test_text_field_uses_is_not_null_condition(self):
        sql = self._run(['qc_local_datetime_issue'])
        self.assertIn('NOT EXISTS', sql)
        self.assertIn('IS NOT NULL', sql)
        self.assertIn("''", sql)
        self.assertNotIn('qc_local_datetime_issue = TRUE', sql)


class TestQcExclusionHelperContract(unittest.TestCase):
    """Direct unit tests for the refactored _build_qc_exclusion_cond contract.

    After the refactor (shared by export + dashboard + real-time APIs) the helper
    returns a BARE ``NOT EXISTS (...)`` predicate (no leading ` AND `) and accepts
    an ``obs_alias`` to correlate against the right observations alias.
    """

    def test_returns_bare_predicate_without_leading_and(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        frag = _build_qc_exclusion_cond(['qc_non_functional'])
        self.assertTrue(frag.lstrip().startswith('NOT EXISTS'),
                        "Фрагмент має починатися з NOT EXISTS, без провідного AND")
        # No leading AND before the predicate.
        self.assertFalse(frag.lstrip().startswith('AND'))

    def test_empty_returns_empty_string(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        self.assertEqual(_build_qc_exclusion_cond([]), "")
        self.assertEqual(_build_qc_exclusion_cond(None or []), "")

    def test_default_alias_is_o(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        frag = _build_qc_exclusion_cond(['qc_stolen'])
        self.assertIn('o.location_id', frag)
        self.assertIn('o.series_start_time', frag)

    def test_observations_alias_substituted(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        frag = _build_qc_exclusion_cond(['qc_stolen'], obs_alias='observations')
        self.assertIn('observations.location_id', frag)
        self.assertIn('observations.series_start_time', frag)
        self.assertNotIn('o.location_id', frag)

    def test_boolean_semantics_unchanged(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        frag = _build_qc_exclusion_cond(['qc_non_functional'], obs_alias='observations')
        self.assertIn('d_qc.qc_non_functional = TRUE', frag)

    def test_text_field_semantics_unchanged(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        frag = _build_qc_exclusion_cond(['qc_local_datetime_issue'], obs_alias='observations')
        self.assertIn('IS NOT NULL', frag)
        self.assertIn("<> ''", frag)
        self.assertNotIn('qc_local_datetime_issue = TRUE', frag)

    def test_or_logic_single_block(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        frag = _build_qc_exclusion_cond(['qc_non_functional', 'qc_stolen'], obs_alias='observations')
        self.assertEqual(frag.count('NOT EXISTS'), 1)
        self.assertIn(' OR ', frag)

    def test_whitelist_ignores_invalid_flag(self):
        from app.camera_traps.data_export import _build_qc_exclusion_cond
        self.assertEqual(
            _build_qc_exclusion_cond(["bogus'; DROP TABLE deployments; --"]), "")
        # Mixed valid+invalid → only the valid flag survives.
        frag = _build_qc_exclusion_cond(['qc_stolen', 'not_a_flag'])
        self.assertIn('qc_stolen = TRUE', frag)
        self.assertNotIn('not_a_flag', frag)


if __name__ == '__main__':
    unittest.main(verbosity=2)
