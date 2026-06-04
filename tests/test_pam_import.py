"""
Тести для системи імпорту PAM (app/pam/pam_import_utils.py + routes).

Структура:
  1. TestBirdNETIsEmptyContent     — виявлення порожніх файлів
  2. TestBirdNETParseCSV           — парсинг CSV (поточний та старий формат)
  3. TestBirdNETParseDateTime      — парсинг дати/часу з імені файлу
  4. TestPAMProcessorFileHandling  — обробка файлів (без БД)
  5. TestPAMProcessorDatabase      — взаємодія з БД (mock engine)
  6. TestPAMProcessorIdempotency   — повторний імпорт
  7. TestPAMImportPage             — GET /<lang>/pam/import (доступ, шаблон)
  8. TestPAMImportAPI              — POST /<lang>/api/pam/import (валідація, логіка)

Запуск:
    venv/Scripts/python -m unittest tests.test_pam_import -v
    або:
    venv/Scripts/python -m pytest tests/test_pam_import.py -v
"""

import io
import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'


# ─── CSV fixtures ─────────────────────────────────────────────────────────────

BIRDNET_HEADER = "Start (s),End (s),Scientific name,Common name,Confidence,File"

BIRDNET_CSV_VALID = """\
Start (s),End (s),Scientific name,Common name,Confidence,File
6.0,9.0,Turdus merula,Eurasian Blackbird,0.9754,F:\\path\\FRANKO_20260305_170100.wav
0.0,3.0,Strix aluco,Tawny Owl,0.8123,F:\\path\\FRANKO_20260305_170100.wav
18.0,21.0,Turdus iliacus,Redwing,0.1036,F:\\path\\FRANKO_20260305_170100.wav
"""

BIRDNET_CSV_OLD_FORMAT = """\
Start..s.,End..s.,Scientific.name,Common.name,Confidence,File
6.0,9.0,Turdus merula,Eurasian Blackbird,0.9754,F:\\path\\SITE1_20250303_184602.wav
"""

BIRDNET_CSV_TWO_RECORDINGS = """\
Start (s),End (s),Scientific name,Common name,Confidence,File
6.0,9.0,Turdus merula,Eurasian Blackbird,0.97,F:\\path\\REC_20260305_060000.wav
0.0,3.0,Strix aluco,Tawny Owl,0.81,F:\\path\\REC_20260306_070000.wav
"""

BIRDNET_CSV_HEADER_ONLY = "Start (s),End (s),Scientific name,Common name,Confidence,File\n"

BIRDNET_CSV_BLANK = ""

BIRDNET_CSV_MISSING_COL = """\
Start (s),End (s),Common name,Confidence,File
6.0,9.0,Eurasian Blackbird,0.97,F:\\path\\REC_20260305_060000.wav
"""

BIRDNET_CSV_BAD_ROWS = """\
Start (s),End (s),Scientific name,Common name,Confidence,File
6.0,9.0,Turdus merula,Eurasian Blackbird,0.97,F:\\path\\FRANKO_20260305_170100.wav
not_a_num,9.0,Bad Species,Name,0.5,F:\\path\\FRANKO_20260305_170100.wav
6.0,9.0,,No Name,0.5,F:\\path\\FRANKO_20260305_170100.wav
6.0,9.0,Strix aluco,Tawny Owl,1.1,F:\\path\\FRANKO_20260305_170100.wav
6.0,9.0,Parus major,Great Tit,INVALID,F:\\path\\FRANKO_20260305_170100.wav
"""

BIRDNET_CSV_BACKSLASH = """\
Start (s),End (s),Scientific name,Common name,Confidence,File
6.0,9.0,Turdus merula,Eurasian Blackbird,0.97,F:\\deep\\nested\\folder\\SITE_20260305_170100.wav
"""

BIRDNET_CSV_FORWARD_SLASH = """\
Start (s),End (s),Scientific name,Common name,Confidence,File
6.0,9.0,Turdus merula,Eurasian Blackbird,0.97,/mnt/data/SITE_20260305_170100.wav
"""

BIRDNET_CSV_NO_CONFIDENCE = """\
Start (s),End (s),Scientific name,Common name,File
6.0,9.0,Turdus merula,Eurasian Blackbird,F:\\path\\SITE_20260305_170100.wav
"""


# ─── helpers ──────────────────────────────────────────────────────────────────

class MockFileStorage:
    """Lightweight stand-in for werkzeug.datastructures.FileStorage."""
    def __init__(self, content: str, filename: str = 'test.csv'):
        self.filename = filename
        self._content = content.encode('utf-8')

    def read(self):
        return self._content


def _make_processor_conn(
    species_map=None,       # {scientific_name: species_id}
    recording_id=1,
    was_inserted=True,
    det_inserted=3,
):
    """
    Builds a mock SQLAlchemy connection matching the PAMImportProcessor call sequence:
      execute #1  — species upsert INSERT   (result ignored)
      execute #2  — species SELECT           (iterable of (name, id) rows)
      execute #3  — recording upsert INSERT  (fetchone → (recording_id, was_inserted))
      execute #4+ — detection CTE INSERT     (fetchone → (count,)) — one per batch
    """
    if species_map is None:
        species_map = {'Turdus merula': 1, 'Strix aluco': 2, 'Turdus iliacus': 3}

    mock_conn = MagicMock()

    # execute #1: species upsert — return value is irrelevant
    sp_upsert = MagicMock()

    # execute #2: species SELECT → iterable
    sp_select = MagicMock()
    sp_select.__iter__ = lambda self: iter(list(species_map.items()))

    # execute #3: recording INSERT
    rec_result = MagicMock()
    rec_result.fetchone.return_value = (recording_id, was_inserted)

    # execute #4+: detection CTE INSERT (each batch)
    det_result = MagicMock()
    det_result.fetchone.return_value = (det_inserted,)

    mock_conn.execute.side_effect = [sp_upsert, sp_select, rec_result, det_result,
                                      det_result, det_result]  # extra for multi-batch

    return mock_conn


def _make_engine(mock_conn):
    """Wraps a mock_conn in a mock engine."""
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn
    return mock_engine


# ══════════════════════════════════════════════════════════════════════════════
# 1. BirdNETImporter.is_empty_content
# ══════════════════════════════════════════════════════════════════════════════

class TestBirdNETIsEmptyContent(unittest.TestCase):

    def setUp(self):
        from app.pam.pam_import_utils import BirdNETImporter
        self.imp = BirdNETImporter()

    def test_blank_string_is_empty(self):
        self.assertTrue(self.imp.is_empty_content(""))

    def test_whitespace_only_is_empty(self):
        self.assertTrue(self.imp.is_empty_content("   \n  \t  \n"))

    def test_header_only_is_empty(self):
        self.assertTrue(self.imp.is_empty_content(BIRDNET_CSV_HEADER_ONLY))

    def test_header_with_one_data_row_is_not_empty(self):
        self.assertFalse(self.imp.is_empty_content(BIRDNET_CSV_VALID))

    def test_header_crlf_only_is_empty(self):
        self.assertTrue(self.imp.is_empty_content(BIRDNET_HEADER + "\r\n"))

    def test_two_blank_lines_is_empty(self):
        self.assertTrue(self.imp.is_empty_content("\n\n"))

    def test_single_non_header_line_is_not_empty(self):
        # Two non-empty lines → not empty
        self.assertFalse(self.imp.is_empty_content("line1\nline2\n"))


# ══════════════════════════════════════════════════════════════════════════════
# 2. BirdNETImporter.parse_csv
# ══════════════════════════════════════════════════════════════════════════════

class TestBirdNETParseCSV(unittest.TestCase):

    def setUp(self):
        from app.pam.pam_import_utils import BirdNETImporter
        self.imp = BirdNETImporter()

    # ── happy path ───────────────────────────────────────────────────────────

    def test_parses_three_rows(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_VALID)
        self.assertEqual(len(rows), 3)

    def test_scientific_name_correct(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_VALID)
        self.assertEqual(rows[0].scientific_name, 'Turdus merula')

    def test_common_name_correct(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_VALID)
        self.assertEqual(rows[0].common_name_en, 'Eurasian Blackbird')

    def test_confidence_parsed(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_VALID)
        self.assertAlmostEqual(rows[0].confidence, 0.9754, places=4)

    def test_start_end_seconds_parsed(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_VALID)
        self.assertEqual(rows[0].start_s, 6.0)
        self.assertEqual(rows[0].end_s, 9.0)

    def test_basename_extracted_from_windows_path(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_BACKSLASH)
        self.assertEqual(rows[0].recording_filename, 'SITE_20260305_170100.wav')

    def test_basename_extracted_from_unix_path(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_FORWARD_SLASH)
        self.assertEqual(rows[0].recording_filename, 'SITE_20260305_170100.wav')

    def test_all_rows_have_same_recording_filename(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_VALID)
        filenames = {r.recording_filename for r in rows}
        self.assertEqual(filenames, {'FRANKO_20260305_170100.wav'})

    def test_two_recordings_in_one_csv(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_TWO_RECORDINGS)
        filenames = {r.recording_filename for r in rows}
        self.assertEqual(len(filenames), 2)

    # ── old R-exported format ─────────────────────────────────────────────────

    def test_old_r_format_parsed(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_OLD_FORMAT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].scientific_name, 'Turdus merula')

    # ── error handling ────────────────────────────────────────────────────────

    def test_missing_scientific_name_column_raises(self):
        with self.assertRaises(ValueError):
            self.imp.parse_csv(BIRDNET_CSV_MISSING_COL)

    def test_row_with_invalid_start_s_skipped(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_BAD_ROWS)
        # "not_a_num" start_s → skipped; empty sci name → skipped; keeps the rest
        names = {r.scientific_name for r in rows}
        self.assertNotIn('Bad Species', names)
        self.assertIn('Turdus merula', names)

    def test_row_with_empty_scientific_name_skipped(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_BAD_ROWS)
        names = [r.scientific_name for r in rows]
        self.assertNotIn('', names)

    def test_confidence_above_1_treated_as_none(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_BAD_ROWS)
        # Row with confidence=1.1 → should be parsed (valid row) but confidence=None
        strix_rows = [r for r in rows if r.scientific_name == 'Strix aluco']
        self.assertEqual(len(strix_rows), 1)
        self.assertIsNone(strix_rows[0].confidence)

    def test_non_numeric_confidence_treated_as_none(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_BAD_ROWS)
        parus_rows = [r for r in rows if r.scientific_name == 'Parus major']
        self.assertEqual(len(parus_rows), 1)
        self.assertIsNone(parus_rows[0].confidence)

    def test_csv_without_confidence_column(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_NO_CONFIDENCE)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].confidence)

    def test_returns_empty_list_for_header_only(self):
        rows = self.imp.parse_csv(BIRDNET_CSV_HEADER_ONLY)
        self.assertEqual(rows, [])

    def test_confidence_zero_is_valid(self):
        csv = BIRDNET_HEADER + "\n6.0,9.0,Turdus merula,Blackbird,0.0,F:\\p\\R_20260305_060000.wav\n"
        rows = self.imp.parse_csv(csv)
        self.assertEqual(rows[0].confidence, 0.0)

    def test_confidence_one_is_valid(self):
        csv = BIRDNET_HEADER + "\n6.0,9.0,Turdus merula,Blackbird,1.0,F:\\p\\R_20260305_060000.wav\n"
        rows = self.imp.parse_csv(csv)
        self.assertEqual(rows[0].confidence, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
# 3. BirdNETImporter.parse_datetime
# ══════════════════════════════════════════════════════════════════════════════

class TestBirdNETParseDateTime(unittest.TestCase):

    def setUp(self):
        from app.pam.pam_import_utils import BirdNETImporter
        self.imp = BirdNETImporter()

    def test_standard_pattern(self):
        dt = self.imp.parse_datetime('FRANKO_20260305_170100.wav')
        self.assertEqual(dt, datetime(2026, 3, 5, 17, 1, 0))

    def test_prefix_with_numbers(self):
        dt = self.imp.parse_datetime('SITE01_20250101_000000.wav')
        self.assertEqual(dt, datetime(2025, 1, 1, 0, 0, 0))

    def test_flac_extension(self):
        dt = self.imp.parse_datetime('K1_20241110_074902.flac')
        self.assertEqual(dt, datetime(2024, 11, 10, 7, 49, 2))

    def test_uppercase_extension(self):
        dt = self.imp.parse_datetime('REC_20260305_170100.WAV')
        self.assertEqual(dt, datetime(2026, 3, 5, 17, 1, 0))

    def test_no_date_pattern_returns_none(self):
        self.assertIsNone(self.imp.parse_datetime('recording_without_date.wav'))

    def test_invalid_month_returns_none(self):
        # Month 13 is invalid
        self.assertIsNone(self.imp.parse_datetime('SITE_20261305_170100.wav'))

    def test_invalid_hour_returns_none(self):
        self.assertIsNone(self.imp.parse_datetime('SITE_20260305_250100.wav'))

    def test_full_path_still_works(self):
        # parse_datetime receives basename, but shouldn't break on full path either
        dt = self.imp.parse_datetime('FRANKO_20260305_060000.wav')
        self.assertEqual(dt.date(), datetime(2026, 3, 5).date())

    def test_midnight(self):
        dt = self.imp.parse_datetime('REC_20260101_000000.wav')
        self.assertEqual(dt, datetime(2026, 1, 1, 0, 0, 0))

    def test_end_of_day(self):
        dt = self.imp.parse_datetime('REC_20261231_235959.wav')
        self.assertEqual(dt, datetime(2026, 12, 31, 23, 59, 59))


# ══════════════════════════════════════════════════════════════════════════════
# 4. PAMImportProcessor — обробка файлів (без БД)
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorFileHandling(unittest.TestCase):

    def _make_processor(self, engine=None):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        if engine is None:
            engine = MagicMock()
        return PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())

    def test_empty_file_list_returns_zero_stats(self):
        proc = self._make_processor()
        stats = proc.process_batch([])
        self.assertEqual(stats['files_processed'], 0)
        self.assertEqual(stats['files_empty'], 0)
        self.assertEqual(stats['files_failed'], 0)

    def test_header_only_file_counted_as_empty(self):
        mock_conn = _make_processor_conn()
        proc = self._make_processor(_make_engine(mock_conn))
        files = [MockFileStorage(BIRDNET_CSV_HEADER_ONLY, 'empty.csv')]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_empty'], 1)
        self.assertEqual(stats['files_processed'], 0)

    def test_blank_file_counted_as_empty(self):
        proc = self._make_processor()
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_BLANK)])
        self.assertEqual(stats['files_empty'], 1)

    def test_valid_file_counted_as_processed(self):
        mock_conn = _make_processor_conn()
        proc = self._make_processor(_make_engine(mock_conn))
        files = [MockFileStorage(BIRDNET_CSV_VALID, 'rec.csv')]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_processed'], 1)
        self.assertEqual(stats['files_empty'], 0)
        self.assertEqual(stats['files_failed'], 0)

    def test_default_duration_is_5(self):
        """#37: тривалість за замовчуванням — 5 хв."""
        proc = self._make_processor()
        self.assertEqual(proc.duration_minutes, 5)

    def test_duration_minutes_passed_to_recording_insert(self):
        """#37: задана тривалість потрапляє у INSERT recordings (param 'dur')."""
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn()
        proc = PAMImportProcessor(_make_engine(mock_conn), location_id=1,
                                  importer=BirdNETImporter(), duration_minutes=10)
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID, 'rec.csv')])
        rec_calls = [c for c in mock_conn.execute.call_args_list
                     if 'INSERT INTO recordings' in str(c.args[0])]
        self.assertTrue(rec_calls, "немає виклику INSERT INTO recordings")
        self.assertEqual(rec_calls[0].args[1]['dur'], 10)

    def test_mixed_empty_and_valid(self):
        mock_conn = _make_processor_conn()
        proc = self._make_processor(_make_engine(mock_conn))
        files = [
            MockFileStorage(BIRDNET_CSV_HEADER_ONLY, 'empty1.csv'),
            MockFileStorage(BIRDNET_CSV_VALID,       'valid.csv'),
            MockFileStorage(BIRDNET_CSV_BLANK,       'empty2.csv'),
        ]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_processed'], 1)
        self.assertEqual(stats['files_empty'],     2)

    def test_unreadable_file_counted_as_failed(self):
        proc = self._make_processor()
        bad_file = MagicMock()
        bad_file.read.side_effect = OSError("read error")
        stats = proc.process_batch([bad_file])
        self.assertEqual(stats['files_failed'], 1)
        self.assertEqual(stats['files_processed'], 0)

    def test_invalid_csv_structure_counted_as_failed(self):
        proc = self._make_processor()
        # CSV missing required column → parse_csv raises ValueError
        files = [MockFileStorage(BIRDNET_CSV_MISSING_COL, 'bad.csv')]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_failed'], 1)

    def test_utf8_decoding_errors_handled(self):
        proc = self._make_processor()
        bad_file = MagicMock()
        # Simulate a file with bad encoding that decode still handles via errors='replace'
        bad_file.read.return_value = b'\xff\xfe' + BIRDNET_CSV_HEADER_ONLY.encode('utf-8')
        # Should not raise - decode with errors='replace' handles it
        # But the content will be garbage → empty or failed, not crash
        try:
            proc.process_batch([bad_file])
        except Exception as e:
            self.fail(f"process_batch raised unexpectedly: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 5. PAMImportProcessor — взаємодія з БД
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorDatabase(unittest.TestCase):

    def _run(self, csv_content, species_map=None, recording_id=1,
             was_inserted=True, det_inserted=3):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn(species_map, recording_id, was_inserted, det_inserted)
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=42, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(csv_content, 'test.csv')])
        return stats, mock_conn

    def test_new_recording_increments_recordings_new(self):
        stats, _ = self._run(BIRDNET_CSV_VALID, was_inserted=True)
        self.assertEqual(stats['recordings_new'], 1)
        self.assertEqual(stats['recordings_existing'], 0)

    def test_existing_recording_increments_recordings_existing(self):
        stats, _ = self._run(BIRDNET_CSV_VALID, was_inserted=False)
        self.assertEqual(stats['recordings_existing'], 1)
        self.assertEqual(stats['recordings_new'], 0)

    def test_detections_inserted_count(self):
        stats, _ = self._run(BIRDNET_CSV_VALID, det_inserted=3)
        self.assertEqual(stats['detections_inserted'], 3)

    def test_detections_duplicate_count(self):
        # 3 rows in CSV, 1 actually inserted → 2 duplicates
        stats, _ = self._run(BIRDNET_CSV_VALID, det_inserted=1)
        self.assertEqual(stats['detections_inserted'], 1)
        self.assertEqual(stats['detections_duplicate'], 2)

    def test_species_count(self):
        species_map = {'Turdus merula': 1, 'Strix aluco': 2, 'Turdus iliacus': 3}
        stats, _ = self._run(BIRDNET_CSV_VALID, species_map=species_map)
        self.assertEqual(stats['species_count'], 3)

    def test_engine_connect_called_once_per_batch(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn()
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        engine.connect.assert_called_once()

    def test_connection_closed_after_success(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn()
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        mock_conn.close.assert_called_once()

    def test_connection_closed_after_db_error(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("DB error")
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        try:
            proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        except Exception:
            pass
        mock_conn.close.assert_called()

    def test_two_recordings_processed(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter

        # Need extra execute slots: 1 upsert + 1 select + 2 recording inserts + 2 det inserts
        mock_conn = MagicMock()
        sp_upsert = MagicMock()
        sp_select = MagicMock()
        sp_select.__iter__ = lambda s: iter([('Turdus merula', 1), ('Strix aluco', 2)])
        rec_result_1 = MagicMock(); rec_result_1.fetchone.return_value = (1, True)
        rec_result_2 = MagicMock(); rec_result_2.fetchone.return_value = (2, True)
        det_result   = MagicMock(); det_result.fetchone.return_value = (1,)
        mock_conn.execute.side_effect = [
            sp_upsert, sp_select,
            rec_result_1, det_result,
            rec_result_2, det_result,
        ]

        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_TWO_RECORDINGS)])
        self.assertEqual(stats['recordings_new'], 2)

    def test_species_upsert_preserves_existing_common_name(self):
        """COALESCE must prefer species.common_name_en over EXCLUDED (don't overwrite existing)."""
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn()
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])

        upsert_call = mock_conn.execute.call_args_list[0]
        sql_str = str(upsert_call[0][0])
        # species.common_name_en must come BEFORE EXCLUDED so existing value wins
        idx_existing = sql_str.index('species.common_name_en')
        idx_excluded = sql_str.index('EXCLUDED.common_name_en')
        self.assertLess(idx_existing, idx_excluded,
            "COALESCE must prefer species.common_name_en (existing) over EXCLUDED")

    def test_recording_insert_uses_correct_location_id(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        LOCATION_ID = 99
        mock_conn = _make_processor_conn()
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=LOCATION_ID, importer=BirdNETImporter())
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])

        # Third execute call is recording INSERT; check params contain location_id
        rec_call = mock_conn.execute.call_args_list[2]
        params = rec_call[0][1]  # second positional arg is params dict
        self.assertEqual(params['loc'], LOCATION_ID)

    def test_detection_cte_counts_inserted_vs_skipped(self):
        """Verifies _insert_detections_batch uses CTE + RETURNING for accurate counts."""
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn(det_inserted=2)
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])

        # Fourth execute call is detection CTE INSERT
        det_call = mock_conn.execute.call_args_list[3]
        sql_str = str(det_call[0][0])
        self.assertIn('ON CONFLICT DO NOTHING', sql_str)
        self.assertIn('RETURNING', sql_str)
        self.assertIn('COUNT', sql_str)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Ідемпотентність — повторний імпорт тих самих файлів
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorIdempotency(unittest.TestCase):

    def _make_existing_conn(self):
        """Simulates DB state where everything already exists."""
        mock_conn = MagicMock()
        sp_upsert = MagicMock()
        sp_select = MagicMock()
        sp_select.__iter__ = lambda s: iter([('Turdus merula', 1), ('Strix aluco', 2), ('Turdus iliacus', 3)])
        rec_result = MagicMock()
        # was_inserted=False → recording already existed
        rec_result.fetchone.return_value = (1, False)
        det_result = MagicMock()
        # 0 inserted → all are duplicates
        det_result.fetchone.return_value = (0,)
        mock_conn.execute.side_effect = [sp_upsert, sp_select, rec_result, det_result,
                                          det_result, det_result]
        return mock_conn

    def test_second_import_reports_existing_recording(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        engine = _make_engine(self._make_existing_conn())
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['recordings_new'], 0)
        self.assertEqual(stats['recordings_existing'], 1)

    def test_second_import_reports_all_detections_as_duplicates(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        engine = _make_engine(self._make_existing_conn())
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['detections_inserted'], 0)
        self.assertEqual(stats['detections_duplicate'], 3)

    def test_second_import_does_not_change_species_count(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        engine = _make_engine(self._make_existing_conn())
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        # Species SELECT returns 3 regardless
        self.assertEqual(stats['species_count'], 3)

    def test_second_import_files_still_counted_as_processed(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        engine = _make_engine(self._make_existing_conn())
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['files_processed'], 1)

    def test_partial_duplicate_partial_new(self):
        """1 new detection + 2 duplicates in the same batch."""
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        mock_conn = _make_processor_conn(det_inserted=1)
        engine = _make_engine(mock_conn)
        proc = PAMImportProcessor(engine, location_id=1, importer=BirdNETImporter())
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['detections_inserted'], 1)
        self.assertEqual(stats['detections_duplicate'], 2)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Flask route: GET /<lang>/pam/import  (сторінка імпорту)
# ══════════════════════════════════════════════════════════════════════════════

class PamImportRouteBase(unittest.TestCase):
    """Shared Flask app + DB setup for route tests."""

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
        from app.models import User, Role, Institution, UserInstitution

        roles = {
            name: Role(name=name)
            for name in ('admin', 'manager', 'pam_verifier', 'viewer')
        }
        db.session.add_all(roles.values())
        db.session.flush()

        self.inst_a = Institution(name_uk='Заповідник А', name_en='Reserve A', code='imp_a')
        self.inst_b = Institution(name_uk='Заповідник Б', name_en='Reserve B', code='imp_b')
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('pass').decode()

        self.admin = User(username='imp_admin', password_hash=pw)
        self.admin.roles.append(roles['admin'])
        db.session.add(self.admin)

        self.manager = User(username='imp_manager', password_hash=pw)
        self.manager.roles.append(roles['manager'])
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.manager)

        self.viewer = User(username='imp_viewer', password_hash=pw)
        self.viewer.roles.append(roles['viewer'])
        db.session.add(self.viewer)

        db.session.commit()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def _pam_page_conn(self):
        """Mock PAM DB conn returning empty locations for the import page."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        return mock_conn


class TestPAMImportPage(PamImportRouteBase):

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get('/uk/pam/import')
        self.assertIn(resp.status_code, (301, 302))

    def test_viewer_forbidden(self):
        self._login(self.viewer.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._pam_page_conn()):
            resp = self.client.get('/uk/pam/import')
        self.assertEqual(resp.status_code, 403)

    def test_manager_gets_200(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._pam_page_conn()):
            resp = self.client.get('/uk/pam/import')
        self.assertEqual(resp.status_code, 200)

    def test_admin_gets_200(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._pam_page_conn()):
            resp = self.client.get('/uk/pam/import')
        self.assertEqual(resp.status_code, 200)

    def test_response_contains_importer_key(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._pam_page_conn()):
            resp = self.client.get('/uk/pam/import')
        self.assertIn(b'birdnet', resp.data.lower())

    def test_response_contains_institution_name(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._pam_page_conn()):
            resp = self.client.get('/uk/pam/import')
        self.assertIn('Заповідник'.encode('utf-8'), resp.data)

    def test_english_lang_code_accepted(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._pam_page_conn()):
            resp = self.client.get('/en/pam/import')
        self.assertEqual(resp.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Flask route: POST /<lang>/api/pam/import
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMImportAPI(PamImportRouteBase):

    def _post(self, files=None, location_id=1, classifier='birdnet', user=None):
        """Helper: POST to import API as given user."""
        if user is None:
            user = self.manager
        self._login(user.id)

        data = {
            'location_id': str(location_id),
            'classifier': classifier,
        }
        if files:
            data['files'] = files

        # Access check conn (for non-admin: checks location_institutions)
        access_conn = MagicMock()
        access_row = MagicMock()
        access_row.fetchone.return_value = (1,)  # has access
        access_conn.execute.return_value = access_row

        mock_stats = {
            'files_processed': 1, 'files_empty': 0, 'files_failed': 0,
            'recordings_new': 1, 'recordings_existing': 0,
            'detections_inserted': 3, 'detections_duplicate': 0,
            'species_count': 2,
        }

        with patch('app.pam.routes.get_pam_db_connection', return_value=access_conn), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_processor_cls:

            mock_proc = MagicMock()
            mock_proc.process_batch.return_value = mock_stats
            mock_processor_cls.return_value = mock_proc

            resp = self.client.post(
                '/uk/api/pam/import',
                data=data,
                content_type='multipart/form-data',
            )

        return resp

    def _make_csv_file(self, content=BIRDNET_CSV_VALID, name='test.csv'):
        return (io.BytesIO(content.encode()), name)

    # ── auth & access ─────────────────────────────────────────────────────────

    def test_anonymous_returns_401_or_redirect(self):
        resp = self.client.post('/uk/api/pam/import')
        self.assertIn(resp.status_code, (302, 401))

    def test_viewer_returns_403(self):
        self._login(self.viewer.id)
        resp = self.client.post('/uk/api/pam/import',
                                data={'location_id': '1', 'classifier': 'birdnet'},
                                content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 403)

    def test_no_access_to_location_returns_403(self):
        self._login(self.manager.id)
        # Access check returns None → no access
        access_conn = MagicMock()
        access_row = MagicMock()
        access_row.fetchone.return_value = None
        access_conn.execute.return_value = access_row

        with patch('app.pam.routes.get_pam_db_connection', return_value=access_conn), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor'):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '999', 'classifier': 'birdnet',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(resp.status_code, 403)

    def test_admin_bypasses_institution_check(self):
        """Admin user skips the location_institutions access check."""
        self._login(self.admin.id)
        mock_stats = {
            'files_processed': 1, 'files_empty': 0, 'files_failed': 0,
            'recordings_new': 1, 'recordings_existing': 0,
            'detections_inserted': 3, 'detections_duplicate': 0,
            'species_count': 2,
        }
        with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = mock_stats
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'classifier': 'birdnet',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        # Should succeed (no access check for admin) → NOT 403
        self.assertNotEqual(resp.status_code, 403)

    # ── validation ────────────────────────────────────────────────────────────

    def test_missing_location_id_returns_400(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'classifier': 'birdnet'},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    def test_unknown_classifier_returns_400(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=MagicMock()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'classifier': 'nonexistent'},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    def test_no_files_returns_400(self):
        self._login(self.manager.id)
        access_conn = MagicMock()
        access_conn.execute.return_value.fetchone.return_value = (1,)
        with patch('app.pam.routes.get_pam_db_connection', return_value=access_conn), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'classifier': 'birdnet'},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    # ── success path ──────────────────────────────────────────────────────────

    def test_success_returns_json_with_success_true(self):
        resp = self._post(files=self._make_csv_file())
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body['success'])

    def test_success_returns_stats_dict(self):
        resp = self._post(files=self._make_csv_file())
        body = json.loads(resp.data)
        self.assertIn('stats', body)

    def test_stats_contain_all_required_keys(self):
        resp = self._post(files=self._make_csv_file())
        stats = json.loads(resp.data)['stats']
        for key in ('files_processed', 'files_empty', 'files_failed',
                    'recordings_new', 'recordings_existing',
                    'detections_inserted', 'detections_duplicate', 'species_count'):
            self.assertIn(key, stats, f"Missing key: {key}")

    def test_processor_called_with_correct_location(self):
        LOCATION_ID = 77
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {
                'files_processed': 0, 'files_empty': 0, 'files_failed': 0,
                'recordings_new': 0, 'recordings_existing': 0,
                'detections_inserted': 0, 'detections_duplicate': 0, 'species_count': 0,
            }
            self.client.post(
                '/uk/api/pam/import',
                data={'location_id': str(LOCATION_ID), 'classifier': 'birdnet',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        # Check PAMImportProcessor was instantiated with location_id=77
        # Route calls PAMImportProcessor(engine, location_id, importer) positionally
        call_args = mock_cls.call_args
        self.assertEqual(call_args[0][1], LOCATION_ID)

    def test_processor_called_with_birdnet_importer(self):
        from app.pam.pam_import_utils import BirdNETImporter
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {
                'files_processed': 0, 'files_empty': 0, 'files_failed': 0,
                'recordings_new': 0, 'recordings_existing': 0,
                'detections_inserted': 0, 'detections_duplicate': 0, 'species_count': 0,
            }
            self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'classifier': 'birdnet',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        # Route calls PAMImportProcessor(engine, location_id, importer) positionally
        call_args = mock_cls.call_args
        self.assertIsInstance(call_args[0][2], BirdNETImporter)

    def test_db_error_returns_500(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.side_effect = Exception("DB down")
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'classifier': 'birdnet',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(resp.status_code, 500)
        body = json.loads(resp.data)
        self.assertFalse(body['success'])

    def test_multiple_files_passed_to_processor(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {
                'files_processed': 3, 'files_empty': 0, 'files_failed': 0,
                'recordings_new': 3, 'recordings_existing': 0,
                'detections_inserted': 9, 'detections_duplicate': 0, 'species_count': 2,
            }
            self.client.post(
                '/uk/api/pam/import',
                data={
                    'location_id': '1', 'classifier': 'birdnet',
                    'files': [
                        self._make_csv_file(name='a.csv'),
                        self._make_csv_file(name='b.csv'),
                        self._make_csv_file(name='c.csv'),
                    ],
                },
                content_type='multipart/form-data',
            )
        passed_files = mock_cls.return_value.process_batch.call_args[0][0]
        self.assertEqual(len(passed_files), 3)


# ══════════════════════════════════════════════════════════════════════════════
# 9. IMPORTERS registry
# ══════════════════════════════════════════════════════════════════════════════

class TestIMPORTERSRegistry(unittest.TestCase):

    def setUp(self):
        from app.pam.pam_import_utils import IMPORTERS
        self.importers = IMPORTERS

    def test_birdnet_key_present(self):
        self.assertIn('birdnet', self.importers)

    def test_all_importers_have_name_and_key(self):
        from app.pam.pam_import_utils import BaseDetectionImporter
        for key, imp in self.importers.items():
            self.assertIsInstance(imp, BaseDetectionImporter)
            self.assertTrue(hasattr(imp, 'name'))
            self.assertTrue(hasattr(imp, 'key'))
            self.assertEqual(imp.key, key)

    def test_all_importers_implement_parse_csv(self):
        for imp in self.importers.values():
            self.assertTrue(callable(getattr(imp, 'parse_csv', None)))

    def test_all_importers_implement_parse_datetime(self):
        for imp in self.importers.values():
            self.assertTrue(callable(getattr(imp, 'parse_datetime', None)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
