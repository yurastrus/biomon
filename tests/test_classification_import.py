"""Тести імпорту зовнішньої класифікації DeepFaune (app/camera_traps/classification_import).

Покривають чисту (без БД) логіку, яка найризикованіша:
  • parse_deepfaune_csv — basename, парс дати, нормалізація, валідація колонок;
  • _aggregate_series — правило тварина > людина > empty, макс score;
  • _match — ключ (filename, captured_at до секунди), unmatched з обох боків.
"""
from datetime import datetime

import pytest

from app.camera_traps.classification_import import (
    parse_deepfaune_csv,
    _aggregate_series,
    _match,
    _dedupe_matched,
)

HEADER = 'filename,date,seqnum,predictionbase,scorebase,prediction,score,top1,count,humancount'


def _csv(*lines):
    return '\n'.join([HEADER, *lines]) + '\n'


# ── parse_deepfaune_csv ────────────────────────────────────────────────────
def test_parse_basic_row():
    csv_text = _csv(
        r'F:\x\0801\DCIM\100_BTCF\IMG_0013.JPG,2025:07:15 12:32:56,1,roe deer,0.91,roe deer,0.91,roe deer,2,0'
    )
    rows, errors = parse_deepfaune_csv(csv_text)
    assert errors == []
    assert len(rows) == 1
    r = rows[0]
    assert r['original_filename'] == 'IMG_0013.JPG'          # basename з Windows-шляху
    assert r['captured_at'] == datetime(2025, 7, 15, 12, 32, 56)
    assert r['base_label'] == 'roe deer'                      # lower-case
    assert r['base_score'] == pytest.approx(0.91)
    assert r['top1_label'] == 'roe deer'
    assert r['animal_count'] == 2
    assert r['human_count'] == 0


def test_parse_missing_columns():
    rows, errors = parse_deepfaune_csv('foo,bar\n1,2\n')
    assert rows == []
    assert errors and 'Бракує колонок' in errors[0]


def test_parse_bad_date_is_reported_not_fatal():
    csv_text = _csv(
        r'C:\a\IMG_1.JPG,NOT A DATE,1,empty,1.0,empty,1.0,empty,0,0',
        r'C:\a\IMG_2.JPG,2025:07:15 12:00:00,1,fox,0.8,fox,0.8,fox,1,0',
    )
    rows, errors = parse_deepfaune_csv(csv_text)
    assert len(rows) == 1                       # лише валідний рядок
    assert rows[0]['original_filename'] == 'IMG_2.JPG'
    assert len(errors) == 1 and 'некоректна дата' in errors[0]


def test_parse_handles_bom_bytes():
    csv_text = _csv(r'C:\a\IMG_1.JPG,2025:01:02 03:04:05,1,empty,1.0,empty,1.0,empty,0,0')
    rows, errors = parse_deepfaune_csv(('﻿' + csv_text).encode('utf-8'))
    assert errors == []
    assert rows[0]['original_filename'] == 'IMG_1.JPG'


# ── _aggregate_series ──────────────────────────────────────────────────────
def test_aggregate_animal_beats_empty_and_picks_max_score():
    pairs = [('empty', 1.0), ('roe deer', 0.6), ('fox', 0.9), ('empty', 1.0)]
    label, score = _aggregate_series(pairs)
    assert label == 'fox'
    assert score == pytest.approx(0.9)


def test_aggregate_human_beats_empty_but_not_animal():
    assert _aggregate_series([('empty', 1.0), ('human', 1.0)])[0] == 'human'
    assert _aggregate_series([('human', 1.0), ('wild boar', 0.5)])[0] == 'wild boar'


def test_aggregate_all_empty():
    label, score = _aggregate_series([('empty', 1.0), ('empty', 1.0)])
    assert label == 'empty'
    assert score == 1.0


def test_aggregate_undefined_counts_as_animal():
    # 'undefined' = тварина є, але класифікатор не впевнений → тваринний tier
    label, _ = _aggregate_series([('empty', 1.0), ('undefined', 0.4)])
    assert label == 'undefined'


# ── _match ─────────────────────────────────────────────────────────────────
def _row(fn, dt, base='fox', score=0.8):
    return {'original_filename': fn, 'captured_at': dt,
            'base_label': base, 'base_score': score,
            'top1_label': base, 'animal_count': 1, 'human_count': 0}


def test_match_by_filename_and_second():
    dt = datetime(2025, 7, 15, 12, 0, 0)
    index = {
        ('img_0001.jpg', dt): (101, 9001),
        ('img_0002.jpg', dt): (102, 9001),
    }
    rows = [_row('IMG_0001.JPG', dt), _row('IMG_9999.JPG', dt)]
    matched, csv_unmatched, db_without = _match(rows, index)

    assert len(matched) == 1
    row, pid, obs = matched[0]
    assert (pid, obs) == (101, 9001)
    assert len(csv_unmatched) == 1 and csv_unmatched[0]['original_filename'] == 'IMG_9999.JPG'
    # IMG_0002 у БД, але нема в CSV
    assert ('img_0002.jpg', dt) in db_without


def test_dedupe_matched_collapses_same_photo_id():
    dt = datetime(2025, 7, 15, 12, 0, 0)
    # два CSV-рядки зматчились на ОДНЕ фото (pid=101) — напр. burst з тією ж секундою
    matched = [
        (_row('IMG_0001.JPG', dt, base='empty', score=1.0), 101, 9001),
        (_row('IMG_0001.JPG', dt, base='fox', score=0.8), 101, 9001),
        (_row('IMG_0002.JPG', dt), 102, 9001),
    ]
    unique, n_dup = _dedupe_matched(matched)
    assert n_dup == 1
    pids = sorted(item[1] for item in unique)
    assert pids == [101, 102]
    # перемагає останній рядок (fox)
    won = next(item for item in unique if item[1] == 101)
    assert won[0]['base_label'] == 'fox'


def test_match_distinguishes_same_name_different_time():
    dt_2021 = datetime(2021, 8, 1, 10, 0, 0)
    dt_2025 = datetime(2025, 8, 1, 10, 0, 0)
    index = {('img_0001.jpg', dt_2021): (1, 1), ('img_0001.jpg', dt_2025): (2, 2)}
    matched, _, _ = _match([_row('IMG_0001.JPG', dt_2025)], index)
    assert len(matched) == 1
    assert matched[0][1] == 2  # саме фото 2025-го
