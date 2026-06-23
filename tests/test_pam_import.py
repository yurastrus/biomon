"""
Tests for the PAM import system (app/pam/pam_import_utils.py + routes).

Structure:
  1. TestBirdNETIsEmptyContent     — detecting empty files
  2. TestBirdNETParseCSV           — CSV parsing (current and legacy format)
  3. TestBirdNETParseDateTime      — parsing date/time from the file name
  4. TestPAMProcessorFileHandling  — file handling (without a DB)
  5. TestPAMProcessorDatabase      — DB interaction (mock engine)
  6. TestPAMProcessorIdempotency   — repeated import
  7. TestPAMImportPage             — GET /<lang>/pam/import (access, template)
  8. TestPAMImportAPI              — POST /<lang>/api/pam/import (validation, logic)

Run:
    venv/Scripts/python -m unittest tests.test_pam_import -v
    or:
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


# ─── Raven Selection Table fixtures (tab-separated) ───────────────────────────

def _raven(header, *rows):
    return header + "\n" + "\n".join(rows) + "\n"

# Chirpity-style: no Species Code column; cumulative Begin/End Time; the
# within-recording offset is in 'File Offset (s)'.
RAVEN_CHIRPITY_HEADER = ("Selection\tView\tChannel\tBegin Time (s)\tEnd Time (s)"
                         "\tLow Freq (Hz)\tHigh Freq (Hz)\tCommon Name\tConfidence"
                         "\tBegin Path\tFile Offset (s)")

def _chirpity_row(sel, begin, end, common, conf, rec, offset):
    return (f"{sel}\tSpectrogram 1\t1\t{begin}\t{end}\t0\t15000\t{common}\t{conf}"
            f"\tF:\\rec\\{rec}\t{offset}")

RAVEN_CHIRPITY_VALID = _raven(
    RAVEN_CHIRPITY_HEADER,
    _chirpity_row(1, 0,   5,   'Great Tit',          0.78, 'ROZ_20230331_185300.wav', 0),
    _chirpity_row(2, 5,   10,  'Eurasian Blackbird', 0.59, 'ROZ_20230331_185300.wav', 5),
    # second recording — Begin/End cumulative, but File Offset resets:
    _chirpity_row(3, 305, 310, 'Tawny Owl',          0.91, 'ROZ_20230331_190302.wav', 5),
)

# BirdNET Analyzer Raven export: has a Species Code column; "nocall" rows.
RAVEN_BIRDNET_HEADER = ("Selection\tView\tChannel\tBegin Time (s)\tEnd Time (s)"
                        "\tLow Freq (Hz)\tHigh Freq (Hz)\tCommon Name\tSpecies Code"
                        "\tConfidence\tBegin Path\tFile Offset (s)")

def _birdnet_raven_row(sel, begin, end, common, code, conf, rec, offset):
    return (f"{sel}\tSpectrogram 1\t1\t{begin}\t{end}\t0\t12000\t{common}\t{code}"
            f"\t{conf}\tF:\\rec\\{rec}\t{offset}")

RAVEN_BIRDNET_NOCALL = _raven(
    RAVEN_BIRDNET_HEADER,
    _birdnet_raven_row(1, 0, 3, 'nocall', 'nocall', 1.0, 'SITE_20230401_060200.wav', 0),
)

RAVEN_BIRDNET_VALID = _raven(
    RAVEN_BIRDNET_HEADER,
    _birdnet_raven_row(1, 0, 3, 'Tawny Owl', 'tawowl1', 0.83, 'SITE_20230401_060200.wav', 0),
    _birdnet_raven_row(2, 3, 6, 'nocall',    'nocall',  1.0,  'SITE_20230401_060200.wav', 3),
)

RAVEN_HEADER_ONLY = RAVEN_CHIRPITY_HEADER + "\n"

RAVEN_MISSING_COL = ("Selection\tCommon Name\tConfidence\tBegin Path\n"
                     "1\tGreat Tit\t0.5\tF:\\rec\\R_20230401_060200.wav\n")


# ─── helpers ──────────────────────────────────────────────────────────────────

class MockFileStorage:
    """Lightweight stand-in for werkzeug.datastructures.FileStorage."""
    def __init__(self, content: str, filename: str = 'test.csv'):
        self.filename = filename
        self._content = content.encode('utf-8')

    def read(self):
        return self._content


from collections import namedtuple

_DetRow = namedtuple('_DetRow',
                     'detection_id recording_id species_id start_s end_s was_inserted')
_CN     = namedtuple('_CN', 'cn species_id')


class FakeConn:
    """
    Keyword-dispatch fake of a SQLAlchemy connection that mirrors the new
    two-phase PAMImportProcessor flow (and ON CONFLICT semantics):

      * INSERT INTO species                  → ignored
      * SELECT scientific_name, species_id   → iterates species_map
      * SELECT lower(common_name_en) …        → iterates common_map rows
      * INSERT INTO recordings … RETURNING   → fetchone (recording_id, was_inserted)
      * INSERT INTO detections … RETURNING   → fetchall, reflecting the params it
                                               was given (one row per VALUES row),
                                               assigning fresh detection_ids and
                                               flagging the first `new_detection_count`
                                               as inserted (rest = duplicate)
      * INSERT INTO detection_models … (CTE) → fetchone (new_links, total_links)

    Counts are driven by `new_detection_count` / `new_link_count` (None = all new).
    """

    def __init__(self, species_map=None, common_map=None, recording_id=1,
                 recording_existed=False, new_detection_count=None,
                 new_link_count=None):
        self.species_map = species_map if species_map is not None else \
            {'Turdus merula': 1, 'Strix aluco': 2, 'Turdus iliacus': 3}
        self.common_map = common_map or {}
        self.recording_id = recording_id
        self.recording_existed = recording_existed
        self.new_detection_count = new_detection_count
        self.new_link_count = new_link_count
        self.calls = []
        self._next_det_id = 1000

    # transaction context manager (conn.begin())
    def begin(self):
        cm = MagicMock()
        cm.__enter__ = lambda *a: None
        cm.__exit__ = lambda *a: False
        return cm

    def close(self):
        pass

    def execute(self, sql, params=None):
        s = str(sql)
        self.calls.append((s, params))

        if 'INSERT INTO species' in s:
            return MagicMock()

        if 'SELECT scientific_name, species_id' in s:
            res = MagicMock()
            res.__iter__ = lambda *_: iter(list(self.species_map.items()))
            return res

        if 'lower(common_name_en)' in s:
            rows = [_CN(k, v) for k, v in self.common_map.items()]
            res = MagicMock()
            res.__iter__ = lambda *_: iter(rows)
            return res

        if 'INSERT INTO recordings' in s:
            res = MagicMock()
            res.fetchone.return_value = (self.recording_id, not self.recording_existed)
            return res

        if 'INSERT INTO detections' in s:
            rows, i = [], 0
            while f'rec{i}' in (params or {}):
                rec, sp = params[f'rec{i}'], params[f'sp{i}']
                st, en = float(params[f's{i}']), float(params[f'e{i}'])
                was = True if self.new_detection_count is None else (i < self.new_detection_count)
                rows.append(_DetRow(self._next_det_id, rec, sp, st, en, was))
                self._next_det_id += 1
                i += 1
            res = MagicMock()
            res.fetchall.return_value = rows
            return res

        if 'INTO detection_models' in s:
            total, i = 0, 0
            while f'd{i}' in (params or {}):
                total += 1
                i += 1
            new = total if self.new_link_count is None else self.new_link_count
            res = MagicMock()
            res.fetchone.return_value = (new, total)
            return res

        return MagicMock()


def _make_engine(conn):
    """Wraps a connection (FakeConn or MagicMock) in a mock engine."""
    mock_engine = MagicMock()
    mock_engine.connect.return_value = conn
    return mock_engine


# Default model wiring for processor tests: model 1 == reference (BirdNET 2.4).
_REF_MODEL = 1


def _make_processor(engine=None, importer=None, location_id=1,
                    model_id=_REF_MODEL, reference_model_id=_REF_MODEL,
                    duration_minutes=5):
    from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
    if engine is None:
        engine = MagicMock()
    if importer is None:
        importer = BirdNETImporter()
    return PAMImportProcessor(engine, location_id=location_id, importer=importer,
                              duration_minutes=duration_minutes, model_id=model_id,
                              reference_model_id=reference_model_id)


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
# 3b. RavenSelectionTableImporter.parse_csv / parse_datetime
# ══════════════════════════════════════════════════════════════════════════════

class TestRavenParseCSV(unittest.TestCase):

    def setUp(self):
        from app.pam.pam_import_utils import RavenSelectionTableImporter
        self.imp = RavenSelectionTableImporter()

    def test_mode_is_common(self):
        self.assertEqual(self.imp.species_lookup_mode, 'common')

    def test_chirpity_parses_all_rows(self):
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        self.assertEqual(len(rows), 3)

    def test_chirpity_no_scientific_name(self):
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        self.assertTrue(all(r.scientific_name is None for r in rows))

    def test_chirpity_common_name(self):
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        self.assertEqual(rows[0].common_name_en, 'Great Tit')

    def test_uses_file_offset_not_cumulative_begin_time(self):
        # Row 3 has cumulative Begin Time 305 but File Offset 5 → start_s must be 5.
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        owl = [r for r in rows if r.common_name_en == 'Tawny Owl'][0]
        self.assertEqual(owl.start_s, 5.0)
        self.assertEqual(owl.end_s, 10.0)   # offset 5 + (310-305) duration

    def test_confidence_parsed(self):
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        self.assertAlmostEqual(rows[0].confidence, 0.78, places=2)

    def test_recording_filename_from_begin_path(self):
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        self.assertEqual(rows[0].recording_filename, 'ROZ_20230331_185300.wav')

    def test_multiple_recordings_in_one_table(self):
        rows = self.imp.parse_csv(RAVEN_CHIRPITY_VALID)
        self.assertEqual(len({r.recording_filename for r in rows}), 2)

    def test_birdnet_raven_skips_nocall(self):
        rows = self.imp.parse_csv(RAVEN_BIRDNET_VALID)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].common_name_en, 'Tawny Owl')

    def test_all_nocall_file_yields_no_rows(self):
        rows = self.imp.parse_csv(RAVEN_BIRDNET_NOCALL)
        self.assertEqual(rows, [])

    def test_high_precision_times_rounded_stable(self):
        content = _raven(
            RAVEN_CHIRPITY_HEADER,
            _chirpity_row(1, 10001.916000000001, 10006.916000000001,
                          'Great Tit', 0.5, 'R_20230401_060200.wav', 12),
        )
        rows = self.imp.parse_csv(content)
        self.assertEqual(rows[0].start_s, 12.0)
        self.assertEqual(rows[0].end_s, 17.0)   # 12 + round(5.0)

    def test_missing_required_column_raises(self):
        with self.assertRaises(ValueError):
            self.imp.parse_csv(RAVEN_MISSING_COL)

    def test_header_only_returns_empty(self):
        self.assertEqual(self.imp.parse_csv(RAVEN_HEADER_ONLY), [])

    def test_parse_datetime(self):
        self.assertEqual(self.imp.parse_datetime('ROZ_20230331_185300.wav'),
                         datetime(2023, 3, 31, 18, 53, 0))


# ══════════════════════════════════════════════════════════════════════════════
# 4. PAMImportProcessor — file handling (without a DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorFileHandling(unittest.TestCase):

    def _make_processor(self, engine=None):
        return _make_processor(engine=engine)

    def test_model_id_required(self):
        from app.pam.pam_import_utils import PAMImportProcessor, BirdNETImporter
        proc = PAMImportProcessor(MagicMock(), location_id=1,
                                  importer=BirdNETImporter())  # no model_id
        with self.assertRaises(ValueError):
            proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])

    def test_empty_file_list_returns_zero_stats(self):
        proc = self._make_processor()
        stats = proc.process_batch([])
        self.assertEqual(stats['files_processed'], 0)
        self.assertEqual(stats['files_empty'], 0)
        self.assertEqual(stats['files_failed'], 0)

    def test_header_only_file_counted_as_empty(self):
        proc = self._make_processor(_make_engine(FakeConn()))
        files = [MockFileStorage(BIRDNET_CSV_HEADER_ONLY, 'empty.csv')]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_empty'], 1)
        self.assertEqual(stats['files_processed'], 0)

    def test_blank_file_counted_as_empty(self):
        proc = self._make_processor(_make_engine(FakeConn()))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_BLANK)])
        self.assertEqual(stats['files_empty'], 1)

    def test_valid_file_counted_as_processed(self):
        proc = self._make_processor(_make_engine(FakeConn()))
        files = [MockFileStorage(BIRDNET_CSV_VALID, 'rec.csv')]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_processed'], 1)
        self.assertEqual(stats['files_empty'], 0)
        self.assertEqual(stats['files_failed'], 0)

    def test_default_duration_is_5(self):
        """#37: default duration is 5 min."""
        proc = self._make_processor()
        self.assertEqual(proc.duration_minutes, 5)

    def test_duration_minutes_passed_to_recording_insert(self):
        """#37: the given duration ends up in INSERT recordings (param 'dur')."""
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn), duration_minutes=10)
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID, 'rec.csv')])
        rec_calls = [c for c in conn.calls if 'INSERT INTO recordings' in c[0]]
        self.assertTrue(rec_calls, "no INSERT INTO recordings call")
        self.assertEqual(rec_calls[0][1]['dur'], 10)

    def test_mixed_empty_and_valid(self):
        proc = self._make_processor(_make_engine(FakeConn()))
        files = [
            MockFileStorage(BIRDNET_CSV_HEADER_ONLY, 'empty1.csv'),
            MockFileStorage(BIRDNET_CSV_VALID,       'valid.csv'),
            MockFileStorage(BIRDNET_CSV_BLANK,       'empty2.csv'),
        ]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_processed'], 1)
        self.assertEqual(stats['files_empty'],     2)

    def test_unreadable_file_counted_as_failed(self):
        proc = self._make_processor(_make_engine(FakeConn()))
        bad_file = MagicMock()
        bad_file.read.side_effect = OSError("read error")
        stats = proc.process_batch([bad_file])
        self.assertEqual(stats['files_failed'], 1)
        self.assertEqual(stats['files_processed'], 0)

    def test_invalid_csv_structure_counted_as_failed(self):
        proc = self._make_processor(_make_engine(FakeConn()))
        # CSV missing required column → parse_csv raises ValueError
        files = [MockFileStorage(BIRDNET_CSV_MISSING_COL, 'bad.csv')]
        stats = proc.process_batch(files)
        self.assertEqual(stats['files_failed'], 1)

    def test_utf8_decoding_errors_handled(self):
        proc = self._make_processor(_make_engine(FakeConn()))
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
# 5. PAMImportProcessor — DB interaction (two-phase + model accounting)
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorDatabase(unittest.TestCase):

    def _run(self, csv_content, conn=None, location_id=42, model_id=_REF_MODEL,
             reference_model_id=_REF_MODEL):
        if conn is None:
            conn = FakeConn()
        engine = _make_engine(conn)
        proc = _make_processor(engine=engine, location_id=location_id,
                               model_id=model_id, reference_model_id=reference_model_id)
        stats = proc.process_batch([MockFileStorage(csv_content, 'test.csv')])
        return stats, conn

    def test_new_recording_increments_recordings_new(self):
        stats, _ = self._run(BIRDNET_CSV_VALID, conn=FakeConn(recording_existed=False))
        self.assertEqual(stats['recordings_new'], 1)
        self.assertEqual(stats['recordings_existing'], 0)

    def test_existing_recording_increments_recordings_existing(self):
        stats, _ = self._run(BIRDNET_CSV_VALID, conn=FakeConn(recording_existed=True))
        self.assertEqual(stats['recordings_existing'], 1)
        self.assertEqual(stats['recordings_new'], 0)

    def test_detections_inserted_count(self):
        stats, _ = self._run(BIRDNET_CSV_VALID)
        self.assertEqual(stats['detections_inserted'], 3)

    def test_detections_duplicate_count(self):
        # 3 rows, only 1 a new event → 2 duplicate events
        stats, _ = self._run(BIRDNET_CSV_VALID, conn=FakeConn(new_detection_count=1))
        self.assertEqual(stats['detections_inserted'], 1)
        self.assertEqual(stats['detections_duplicate'], 2)

    def test_model_links_counted(self):
        stats, _ = self._run(BIRDNET_CSV_VALID)
        # every new detection gets a fresh model link
        self.assertEqual(stats['model_links_new'], 3)
        self.assertEqual(stats['model_links_existing'], 0)

    def test_existing_model_links_counted(self):
        stats, _ = self._run(BIRDNET_CSV_VALID, conn=FakeConn(new_link_count=0))
        self.assertEqual(stats['model_links_new'], 0)
        self.assertEqual(stats['model_links_existing'], 3)

    def test_species_count(self):
        stats, _ = self._run(BIRDNET_CSV_VALID)
        self.assertEqual(stats['species_count'], 3)

    def test_reference_model_writes_detection_confidence(self):
        """For the reference model, the detections INSERT carries real confidence values."""
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn), model_id=1, reference_model_id=1)
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        det_call = [c for c in conn.calls if 'INSERT INTO detections' in c[0]][0]
        params = det_call[1]
        conf_values = [v for k, v in params.items() if k.startswith('c')]
        self.assertTrue(any(v is not None for v in conf_values),
                        "reference model must write confidence into detections")

    def test_non_reference_model_writes_null_detection_confidence(self):
        """A non-reference model must NOT write into detections.confidence (NULL on insert)."""
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn), model_id=2, reference_model_id=1)
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        det_call = [c for c in conn.calls if 'INSERT INTO detections' in c[0]][0]
        params = det_call[1]
        conf_values = [v for k, v in params.items() if k.startswith('c')]
        self.assertTrue(all(v is None for v in conf_values),
                        "non-reference model must leave detections.confidence NULL")
        # …but per-model confidence still flows into detection_models:
        dm_call = [c for c in conn.calls if 'INTO detection_models' in c[0]][0]
        dm_conf = [v for k, v in dm_call[1].items() if k.startswith('dc')]
        self.assertTrue(any(v is not None for v in dm_conf),
                        "detection_models must store per-model confidence")

    def test_detection_upsert_targets_real_unique_key(self):
        """The detections upsert must target (recording_id, species_id, start_s, end_s)."""
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn))
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        det_sql = [c[0] for c in conn.calls if 'INSERT INTO detections' in c[0]][0]
        self.assertIn('ON CONFLICT (recording_id, species_id, start_s, end_s)', det_sql)
        self.assertIn('RETURNING', det_sql)

    def test_detection_models_upsert_present(self):
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn))
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        dm_sql = [c[0] for c in conn.calls if 'INTO detection_models' in c[0]]
        self.assertTrue(dm_sql, "no detection_models upsert issued")
        self.assertIn('ON CONFLICT (detection_id, model_id)', dm_sql[0])

    def test_engine_connect_called_once_per_batch(self):
        conn = FakeConn()
        engine = _make_engine(conn)
        proc = _make_processor(engine=engine)
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        engine.connect.assert_called_once()

    def test_connection_closed_after_db_error(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("DB error")
        engine = _make_engine(conn)
        proc = _make_processor(engine=engine)
        try:
            proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        except Exception:
            pass
        conn.close.assert_called()

    def test_two_recordings_processed(self):
        conn = FakeConn(species_map={'Turdus merula': 1, 'Strix aluco': 2})
        proc = _make_processor(engine=_make_engine(conn))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_TWO_RECORDINGS)])
        self.assertEqual(stats['recordings_new'], 2)

    def test_species_upsert_preserves_existing_common_name(self):
        """COALESCE must prefer species.common_name_en over EXCLUDED (don't overwrite existing)."""
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn))
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        sql_str = [c[0] for c in conn.calls if 'INSERT INTO species' in c[0]][0]
        idx_existing = sql_str.index('species.common_name_en')
        idx_excluded = sql_str.index('EXCLUDED.common_name_en')
        self.assertLess(idx_existing, idx_excluded,
            "COALESCE must prefer species.common_name_en (existing) over EXCLUDED")

    def test_recording_insert_uses_correct_location_id(self):
        LOCATION_ID = 99
        conn = FakeConn()
        proc = _make_processor(engine=_make_engine(conn), location_id=LOCATION_ID)
        proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        rec_call = [c for c in conn.calls if 'INSERT INTO recordings' in c[0]][0]
        self.assertEqual(rec_call[1]['loc'], LOCATION_ID)


# ══════════════════════════════════════════════════════════════════════════════
# 5b. Raven import — species resolution by common name
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorRavenCommonName(unittest.TestCase):

    def _proc(self, conn, model_id=2, reference_model_id=1):
        from app.pam.pam_import_utils import RavenSelectionTableImporter
        return _make_processor(engine=_make_engine(conn),
                               importer=RavenSelectionTableImporter(),
                               model_id=model_id, reference_model_id=reference_model_id)

    def test_resolves_by_common_name(self):
        # DB has these common names (lower-cased); 'Eurasian Blackbird' is missing.
        conn = FakeConn(common_map={'great tit': 10, 'tawny owl': 11})
        stats = self._proc(conn).process_batch([MockFileStorage(RAVEN_CHIRPITY_VALID, 'r.txt')])
        # 3 rows: Great Tit (match), Eurasian Blackbird (miss), Tawny Owl (match)
        self.assertEqual(stats['detections_inserted'], 2)
        self.assertEqual(stats['rows_skipped_unknown_species'], 1)

    def test_skipped_species_reported(self):
        conn = FakeConn(common_map={'great tit': 10, 'tawny owl': 11})
        stats = self._proc(conn).process_batch([MockFileStorage(RAVEN_CHIRPITY_VALID, 'r.txt')])
        self.assertIn('Eurasian Blackbird', stats['skipped_species'])
        self.assertEqual(stats['skipped_species']['Eurasian Blackbird'], 1)

    def test_no_species_created_in_common_mode(self):
        conn = FakeConn(common_map={'great tit': 10, 'tawny owl': 11})
        self._proc(conn).process_batch([MockFileStorage(RAVEN_CHIRPITY_VALID, 'r.txt')])
        self.assertFalse(any('INSERT INTO species' in c[0] for c in conn.calls),
                         "common-name mode must never create species rows")

    def test_all_unmatched_skips_everything(self):
        conn = FakeConn(common_map={})  # nothing matches
        stats = self._proc(conn).process_batch([MockFileStorage(RAVEN_CHIRPITY_VALID, 'r.txt')])
        self.assertEqual(stats['detections_inserted'], 0)
        self.assertEqual(stats['rows_skipped_unknown_species'], 3)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Idempotency — re-importing the same files
# ══════════════════════════════════════════════════════════════════════════════

class TestPAMProcessorIdempotency(unittest.TestCase):

    def _existing_conn(self):
        """DB state where the recording, all detections and all links already exist."""
        return FakeConn(recording_existed=True, new_detection_count=0, new_link_count=0)

    def test_second_import_reports_existing_recording(self):
        proc = _make_processor(engine=_make_engine(self._existing_conn()))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['recordings_new'], 0)
        self.assertEqual(stats['recordings_existing'], 1)

    def test_second_import_reports_all_detections_as_duplicates(self):
        proc = _make_processor(engine=_make_engine(self._existing_conn()))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['detections_inserted'], 0)
        self.assertEqual(stats['detections_duplicate'], 3)

    def test_second_import_adds_no_new_model_links(self):
        proc = _make_processor(engine=_make_engine(self._existing_conn()))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['model_links_new'], 0)
        self.assertEqual(stats['model_links_existing'], 3)

    def test_second_import_does_not_change_species_count(self):
        proc = _make_processor(engine=_make_engine(self._existing_conn()))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['species_count'], 3)

    def test_second_import_files_still_counted_as_processed(self):
        proc = _make_processor(engine=_make_engine(self._existing_conn()))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['files_processed'], 1)

    def test_second_model_on_existing_events_adds_links_not_detections(self):
        """A different model over events that already exist: 0 new detections, new links."""
        conn = FakeConn(recording_existed=True, new_detection_count=0, new_link_count=3)
        proc = _make_processor(engine=_make_engine(conn), model_id=2, reference_model_id=1)
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['detections_inserted'], 0)
        self.assertEqual(stats['model_links_new'], 3)

    def test_partial_duplicate_partial_new(self):
        """1 new detection + 2 duplicates in the same batch."""
        conn = FakeConn(new_detection_count=1)
        proc = _make_processor(engine=_make_engine(conn))
        stats = proc.process_batch([MockFileStorage(BIRDNET_CSV_VALID)])
        self.assertEqual(stats['detections_inserted'], 1)
        self.assertEqual(stats['detections_duplicate'], 2)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Flask route: GET /<lang>/pam/import  (import page)
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

_RouteModel = namedtuple('_RouteModel', 'model_id name version')
_ROUTE_MODELS = [
    _RouteModel(1, 'BirdNET', '2.4'),
    _RouteModel(2, 'Perch', 'v2'),
    _RouteModel(3, 'Nocmig', ''),
    _RouteModel(4, 'Nocmig', 'V2 Beta'),
]


def _route_conn(has_access=True):
    """A get_pam_db_connection() stand-in: returns the models catalogue for the
    models query and an access row for the location_institutions query."""
    conn = MagicMock()

    def _ex(sql, params=None):
        s = str(sql)
        res = MagicMock()
        if 'FROM models' in s:
            res.fetchall.return_value = _ROUTE_MODELS
        elif 'location_institutions' in s:
            res.fetchone.return_value = (1,) if has_access else None
        else:
            res.fetchall.return_value = []
            res.fetchone.return_value = None
        return res

    conn.execute.side_effect = _ex
    return conn


class TestPAMImportAPI(PamImportRouteBase):

    def _post(self, files=None, location_id=1, fmt='birdnet', model_id='1', user=None):
        """Helper: POST to import API as given user."""
        if user is None:
            user = self.manager
        self._login(user.id)

        data = {'location_id': str(location_id), 'format': fmt, 'model_id': str(model_id)}
        if files:
            data['files'] = files

        mock_stats = {
            'files_processed': 1, 'files_empty': 0, 'files_failed': 0,
            'recordings_new': 1, 'recordings_existing': 0,
            'detections_inserted': 3, 'detections_duplicate': 0,
            'model_links_new': 3, 'model_links_existing': 0,
            'rows_skipped_unknown_species': 0, 'skipped_species': {},
            'species_count': 2,
        }

        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
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
                                data={'location_id': '1', 'format': 'birdnet', 'model_id': '1'},
                                content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 403)

    def test_no_access_to_location_returns_403(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn(has_access=False)), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor'):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '999', 'format': 'birdnet', 'model_id': '1',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(resp.status_code, 403)

    def test_admin_bypasses_institution_check(self):
        """Admin user skips the location_institutions access check."""
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 0}
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'birdnet', 'model_id': '1',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertNotEqual(resp.status_code, 403)

    # ── validation ────────────────────────────────────────────────────────────

    def test_missing_location_id_returns_400(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'format': 'birdnet', 'model_id': '1'},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    def test_unknown_format_returns_400(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'nonexistent', 'model_id': '1'},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    def test_invalid_model_id_returns_400(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor'):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'birdnet', 'model_id': '999',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    def test_missing_model_id_returns_400(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor'):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'birdnet',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(resp.status_code, 400)

    def test_no_files_returns_400(self):
        self._login(self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()):
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'birdnet', 'model_id': '1'},
                content_type='multipart/form-data',
            )
        body = json.loads(resp.data)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(body['success'])

    def test_legacy_classifier_field_still_accepted(self):
        """Backward compat: old 'classifier' field works when 'format' is absent."""
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 0}
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'classifier': 'birdnet', 'model_id': '1',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(resp.status_code, 200)

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

    def test_raven_format_uses_raven_importer(self):
        from app.pam.pam_import_utils import RavenSelectionTableImporter
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 0}
            self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'raven', 'model_id': '2',
                      'files': (io.BytesIO(RAVEN_CHIRPITY_VALID.encode()), 'r.txt')},
                content_type='multipart/form-data',
            )
        self.assertIsInstance(mock_cls.call_args[0][2], RavenSelectionTableImporter)

    def test_processor_called_with_model_id_and_reference(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 0}
            self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'raven', 'model_id': '2',
                      'files': (io.BytesIO(RAVEN_CHIRPITY_VALID.encode()), 'r.txt')},
                content_type='multipart/form-data',
            )
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs['model_id'], 2)
        self.assertEqual(kwargs['reference_model_id'], 1)   # BirdNET 2.4

    def test_processor_called_with_correct_location(self):
        LOCATION_ID = 77
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 0}
            self.client.post(
                '/uk/api/pam/import',
                data={'location_id': str(LOCATION_ID), 'format': 'birdnet', 'model_id': '1',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(mock_cls.call_args[0][1], LOCATION_ID)

    def test_processor_called_with_birdnet_importer(self):
        from app.pam.pam_import_utils import BirdNETImporter
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 0}
            self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'birdnet', 'model_id': '1',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertIsInstance(mock_cls.call_args[0][2], BirdNETImporter)

    def test_db_error_returns_500(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.side_effect = Exception("DB down")
            resp = self.client.post(
                '/uk/api/pam/import',
                data={'location_id': '1', 'format': 'birdnet', 'model_id': '1',
                      'files': self._make_csv_file()},
                content_type='multipart/form-data',
            )
        self.assertEqual(resp.status_code, 500)
        body = json.loads(resp.data)
        self.assertFalse(body['success'])

    def test_multiple_files_passed_to_processor(self):
        self._login(self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=_route_conn()), \
             patch('app.pam.routes.get_pam_engine', return_value=MagicMock()), \
             patch('app.pam.routes.PAMImportProcessor') as mock_cls:
            mock_cls.return_value.process_batch.return_value = {'species_count': 2}
            self.client.post(
                '/uk/api/pam/import',
                data={
                    'location_id': '1', 'format': 'birdnet', 'model_id': '1',
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

    def test_raven_key_present(self):
        self.assertIn('raven', self.importers)

    def test_birdnet_is_scientific_mode(self):
        self.assertEqual(self.importers['birdnet'].species_lookup_mode, 'scientific')

    def test_raven_is_common_mode(self):
        self.assertEqual(self.importers['raven'].species_lookup_mode, 'common')

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
