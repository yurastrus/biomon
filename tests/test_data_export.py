"""
Тести для app/camera_traps/data_export.py

Запуск:
    cd biomon
    venv/Scripts/python -m pytest tests/test_data_export.py -v
    або:
    venv/Scripts/python -m unittest tests.test_data_export -v
"""
import unittest
from unittest.mock import patch, MagicMock, call
from datetime import datetime


def _make_mock_row(**kwargs):
    """Повертає mock-рядок результату БД."""
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
    Будує повний mock для engine.connect() як context manager.
    Повертає (mock_engine, mock_conn).
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
    """Перевіряє структуру та вміст результату."""

    def setUp(self):
        # Flask app context потрібен для current_app та User.query
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
        """Перевіряє наявність обов'язкових Darwin Core полів."""
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
        """Перевіряє що identifier_user_ids перетворюється на імена."""
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
        """Якщо scientific_name з одного слова — specificEpithet має бути None."""
        row = _make_mock_row(scientific_name='Animalia')
        mock_engine, _ = _make_engine_mock(rows=[row])

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            result = get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        self.assertIsNone(result['data'][0]['specificEpithet'])

    def test_limit_applied(self):
        """Перевіряє що LIMIT додається до запиту при передачі limit."""
        mock_engine, mock_conn = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'}, limit=10)

        # Другий виклик execute — це data query. Перевіряємо що params містить 'limit'.
        second_call_params = mock_conn.execute.call_args_list[1][0][1]
        self.assertIn('limit', second_call_params)
        self.assertEqual(second_call_params['limit'], 10)


class TestGetCtOccurrenceDataConnectionLifecycle(unittest.TestCase):
    """
    Ключові тести: перевіряє що з'єднання завжди закривається.
    Саме це виправляє баг з 'idle in transaction'.
    """

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_connection_closed_on_success(self):
        """З'єднання закривається після успішного виконання."""
        mock_engine, _ = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine), \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        # __exit__ context manager'а має бути викликаний (це закриває з'єднання)
        mock_engine.connect.return_value.__exit__.assert_called_once()

    def test_connection_closed_on_db_error(self):
        """З'єднання закривається навіть якщо БД кинула помилку."""
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

        # Навіть при помилці — __exit__ має бути викликаний
        mock_ctx.__exit__.assert_called_once()

    def test_uses_engine_not_session(self):
        """Перевіряє що функція використовує get_ct_engine, а не get_ct_session."""
        mock_engine, _ = _make_engine_mock()

        with patch('app.camera_traps.data_export.get_ct_engine', return_value=mock_engine) as mock_get_engine, \
             patch('app.camera_traps.data_export.User') as mock_user:
            mock_user.query.filter.return_value.all.return_value = []

            from app.camera_traps.data_export import get_ct_occurrence_data
            get_ct_occurrence_data({'start_date': '2024-01-01', 'end_date': '2024-12-31'})

        mock_get_engine.assert_called_once()
        # get_ct_session не повинна викликатись — її вже немає в імпортах
        mock_engine.connect.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
