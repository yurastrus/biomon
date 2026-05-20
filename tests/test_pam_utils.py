"""
Тести допоміжних функцій модуля pam.

Підмінюємо `get_pam_db_connection` на mock — не торкаємось реальної PAM-БД.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.pam.utils import get_available_species


def _make_mock_conn(rows):
    """Створює фейкове з'єднання, де conn.execute(...).mappings().fetchall() → rows."""
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
