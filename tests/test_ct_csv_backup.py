# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the per-institution CSV backup layer (app/backup/).

The occurrence query itself is covered by tests/test_data_export.py; here it is
patched out, so what is under test is the backup logic proper: folder and file
naming, the change check, version rotation, per-backend isolation, and the
guarantee that one bad institution cannot take the run down.
"""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.backup import ct_csv
from app.backup.storage import (LocalStorage, RcloneStorage, StorageError,
                                build_backends, slugify_folder)


def _institution(id_=1, code='RSNR', name_en='Roztochya Nature Reserve',
                 name_uk='Розточчя'):
    return SimpleNamespace(id=id_, code=code, name_en=name_en, name_uk=name_uk)


def _rows(n=2, species='Canis lupus'):
    return [{'occurrenceID': f'URN:ctmon:RSNR:observation:{i}',
             'observationID': i,
             'scientificName': species,
             'eventDate': '2026-08-01'} for i in range(1, n + 1)]


# ── slugify / folder naming ──────────────────────────────────────────────────

@pytest.mark.parametrize('name,expected', [
    ('Roztochya Nature Reserve', 'Roztochya_Nature_Reserve'),
    ('Carpathian NNP (Gorgany)', 'Carpathian_NNP_Gorgany'),
    ('  spaced  out  ', 'spaced_out'),
    ('Розточчя', 'unknown'),          # non-ASCII collapses; fallback kicks in
    ('', 'unknown'),
    (None, 'unknown'),
])
def test_slugify_folder(name, expected):
    assert slugify_folder(name) == expected


def test_folder_falls_back_to_ukrainian_then_id():
    """A park with no English name must still get a stable, unique folder."""
    inst = _institution(id_=7, name_en=None, name_uk='Розточчя')
    assert ct_csv._folder_for(inst) == 'institution_7'


# ── CSV rendering ────────────────────────────────────────────────────────────

def test_render_csv_header_and_rows():
    payload = ct_csv.render_csv(_rows(2))
    lines = payload.decode('utf-8').strip().split('\n')
    assert lines[0] == 'occurrenceID,observationID,scientificName,eventDate'
    assert len(lines) == 3
    assert 'Canis lupus' in lines[1]


def test_render_csv_empty_returns_none():
    assert ct_csv.render_csv([]) is None


# ── LocalStorage ─────────────────────────────────────────────────────────────

def test_local_put_and_manifest_roundtrip(tmp_path):
    store = LocalStorage(tmp_path)
    store.put('Park', 'a.csv', b'hello')
    assert (tmp_path / 'Park' / 'a.csv').read_bytes() == b'hello'

    store.write_manifest('Park', {'sha256': 'abc'})
    assert store.read_manifest('Park') == {'sha256': 'abc'}


def test_local_read_manifest_missing_or_corrupt(tmp_path):
    store = LocalStorage(tmp_path)
    assert store.read_manifest('Nope') == {}

    (tmp_path / 'Park').mkdir()
    (tmp_path / 'Park' / 'manifest.json').write_text('{ not json', encoding='utf-8')
    assert store.read_manifest('Park') == {}, 'corrupt manifest must not raise'


def test_local_rotate_keeps_newest(tmp_path):
    store = LocalStorage(tmp_path)
    for day in ('2026-08-30', '2026-08-31', '2026-09-01', '2026-09-02'):
        store.put('Park', f'RSNR_ct_occurrence_{day}.csv', b'x')
    store.put('Park', 'unrelated.csv', b'x')

    removed = store.rotate('Park', 'RSNR_ct_occurrence_*.csv', keep=2)

    left = sorted(p.name for p in (tmp_path / 'Park').iterdir())
    assert left == ['RSNR_ct_occurrence_2026-09-01.csv',
                    'RSNR_ct_occurrence_2026-09-02.csv',
                    'unrelated.csv'], 'rotation must not touch other files'
    assert sorted(removed) == ['RSNR_ct_occurrence_2026-08-30.csv',
                               'RSNR_ct_occurrence_2026-08-31.csv']


def test_local_rotate_never_deletes_everything(tmp_path):
    """keep=0 would mean "no backup at all" — clamp it to one surviving file."""
    store = LocalStorage(tmp_path)
    store.put('Park', 'RSNR_ct_occurrence_2026-09-02.csv', b'x')
    store.rotate('Park', 'RSNR_ct_occurrence_*.csv', keep=0)
    assert list((tmp_path / 'Park').glob('*.csv'))


# ── backend factory ──────────────────────────────────────────────────────────

def test_build_backends_skips_disabled_and_unknown(tmp_path):
    backends = build_backends([
        {'type': 'local', 'root': str(tmp_path)},
        {'type': 'rclone', 'enabled': False, 'remote': 'gdrive:x'},
        {'type': 'ftp', 'enabled': True},
        {'type': 'local'},  # missing 'root' — bad config, must not raise
    ])
    assert [b.name for b in backends] == ['local']


def test_rclone_backend_shape():
    """rcat/lsjson/deletefile are the contract; assert we call exactly those."""
    store = RcloneStorage('gdrive:ct', config_path='/tmp/rclone.conf')
    with patch.object(store, '_run', return_value=b'') as run:
        store.put('Park', 'a.csv', b'data')
    run.assert_called_once_with(['rcat', 'gdrive:ct/Park/a.csv'], stdin_data=b'data')

    listing = json.dumps([
        {'Name': 'RSNR_ct_occurrence_2026-09-01.csv', 'IsDir': False},
        {'Name': 'RSNR_ct_occurrence_2026-09-02.csv', 'IsDir': False},
        {'Name': 'RSNR_ct_occurrence_2026-08-31.csv', 'IsDir': False},
        {'Name': 'manifest.json', 'IsDir': False},
    ]).encode()
    with patch.object(store, '_run', side_effect=[listing, b'']) as run:
        removed = store.rotate('Park', 'RSNR_ct_occurrence_*.csv', keep=2)
    assert removed == ['RSNR_ct_occurrence_2026-08-31.csv']


def test_rclone_missing_manifest_is_empty_not_an_error():
    store = RcloneStorage('gdrive:ct')
    with patch.object(store, '_run', side_effect=StorageError('not found')):
        assert store.read_manifest('Park') == {}


# ── export_institution ───────────────────────────────────────────────────────

@pytest.fixture
def occurrence(app):
    """Patch the occurrence query and give tests an app context."""
    with app.app_context():
        with patch('app.camera_traps.data_export.get_ct_occurrence_data') as mock:
            mock.return_value = {'data': _rows(2), 'total_count': 2}
            yield mock


def test_export_writes_file_and_manifest(tmp_path, occurrence):
    store = LocalStorage(tmp_path)
    result = ct_csv.export_institution(_institution(), [store], keep=2,
                                       run_date=date(2026, 9, 2))

    target = tmp_path / 'Roztochya_Nature_Reserve' / 'RSNR_ct_occurrence_2026-09-02.csv'
    assert target.is_file()
    assert result.row_count == 2 and not result.errors

    manifest = store.read_manifest('Roztochya_Nature_Reserve')
    assert manifest['file'] == 'RSNR_ct_occurrence_2026-09-02.csv'
    assert manifest['row_count'] == 2
    assert len(manifest['sha256']) == 64


def test_export_uses_backup_filters_not_page_defaults(tmp_path, occurrence):
    """A backup must cover all history and every row kind, not the UI defaults."""
    ct_csv.export_institution(_institution(), [LocalStorage(tmp_path)], keep=2,
                              run_date=date(2026, 9, 2))
    filters = occurrence.call_args.args[0]
    assert filters['start_date'] == '1900-01-01'
    assert filters['end_date'] == '2026-09-02'
    assert filters['export_mode'] == 'human_ai'
    assert filters['filter_type'] == 'all'
    assert filters['aggregation'] == 'none'
    assert filters['institution_ids'] == [1]
    assert filters['institution_code'] == 'RSNR'


def test_unchanged_data_is_not_written_again(tmp_path, occurrence):
    store = LocalStorage(tmp_path)
    ct_csv.export_institution(_institution(), [store], keep=2,
                              run_date=date(2026, 9, 2))
    second = ct_csv.export_institution(_institution(), [store], keep=2,
                                       run_date=date(2026, 9, 3))

    assert second.skipped_unchanged is True
    files = sorted(p.name for p in (tmp_path / 'Roztochya_Nature_Reserve').glob('*.csv'))
    assert files == ['RSNR_ct_occurrence_2026-09-02.csv'], \
        'identical data must not produce a second dated file'


def test_changed_data_produces_a_new_dated_file(tmp_path, occurrence):
    store = LocalStorage(tmp_path)
    ct_csv.export_institution(_institution(), [store], keep=2,
                              run_date=date(2026, 9, 2))

    occurrence.return_value = {'data': _rows(3, species='Lynx lynx'), 'total_count': 3}
    result = ct_csv.export_institution(_institution(), [store], keep=2,
                                       run_date=date(2026, 9, 3))

    assert result.skipped_unchanged is False
    files = sorted(p.name for p in (tmp_path / 'Roztochya_Nature_Reserve').glob('*.csv'))
    assert files == ['RSNR_ct_occurrence_2026-09-02.csv',
                     'RSNR_ct_occurrence_2026-09-03.csv']


def test_rotation_applies_after_repeated_changes(tmp_path, occurrence):
    store = LocalStorage(tmp_path)
    for n, day in enumerate((1, 2, 3, 4), start=2):
        occurrence.return_value = {'data': _rows(n), 'total_count': n}
        ct_csv.export_institution(_institution(), [store], keep=2,
                                  run_date=date(2026, 9, day))

    files = sorted(p.name for p in (tmp_path / 'Roztochya_Nature_Reserve').glob('*.csv'))
    assert files == ['RSNR_ct_occurrence_2026-09-03.csv',
                     'RSNR_ct_occurrence_2026-09-04.csv']


def test_empty_result_writes_nothing(tmp_path, occurrence):
    occurrence.return_value = {'data': [], 'total_count': 0}
    result = ct_csv.export_institution(_institution(), [LocalStorage(tmp_path)],
                                       keep=2, run_date=date(2026, 9, 2))
    assert result.row_count == 0 and result.filename is None
    assert not list(tmp_path.rglob('*.csv'))


def test_dry_run_touches_nothing(tmp_path, occurrence):
    result = ct_csv.export_institution(_institution(), [LocalStorage(tmp_path)],
                                       keep=2, run_date=date(2026, 9, 2),
                                       dry_run=True)
    assert not list(tmp_path.rglob('*.csv'))
    assert result.locations and 'dry-run' in result.locations[0]


def test_failing_backend_does_not_block_the_healthy_one(tmp_path, occurrence):
    good = LocalStorage(tmp_path)
    bad = MagicMock(spec=RcloneStorage)
    bad.name = 'rclone'
    bad.read_manifest.return_value = {}
    bad.put.side_effect = StorageError('remote unreachable')

    result = ct_csv.export_institution(_institution(), [bad, good], keep=2,
                                       run_date=date(2026, 9, 2))

    assert result.errors and 'remote unreachable' in result.errors[0]
    assert (tmp_path / 'Roztochya_Nature_Reserve'
            / 'RSNR_ct_occurrence_2026-09-02.csv').is_file()


def test_backends_track_their_own_manifest(tmp_path, occurrence):
    """A remote that was down yesterday must catch up, even though local is current."""
    local = LocalStorage(tmp_path / 'local')
    remote = LocalStorage(tmp_path / 'remote')
    remote.name = 'second'

    ct_csv.export_institution(_institution(), [local], keep=2,
                              run_date=date(2026, 9, 2))
    ct_csv.export_institution(_institution(), [local, remote], keep=2,
                              run_date=date(2026, 9, 3))

    assert (tmp_path / 'remote' / 'Roztochya_Nature_Reserve'
            / 'RSNR_ct_occurrence_2026-09-03.csv').is_file()
    assert sorted(p.name for p in
                  (tmp_path / 'local' / 'Roztochya_Nature_Reserve').glob('*.csv')) == \
        ['RSNR_ct_occurrence_2026-09-02.csv']


# ── run_backup orchestration ─────────────────────────────────────────────────

def _run_backup(app, tmp_path, institutions, **kwargs):
    config = {'KEEP_VERSIONS': 2,
              'STORAGES': [{'type': 'local', 'root': str(tmp_path)}]}
    with app.app_context():
        with patch('app.backup.ct_csv.list_ct_institutions', return_value=institutions), \
             patch('app.camera_traps.database.get_ct_engine', return_value=MagicMock()):
            return ct_csv.run_backup(config, run_date=date(2026, 9, 2), **kwargs)


def test_run_backup_isolates_a_failing_institution(app, tmp_path):
    good = _institution(1, 'RSNR', 'Roztochya Nature Reserve')
    bad = _institution(2, 'CNNP', 'Carpathian NNP')

    def side_effect(filters, limit=None):
        if filters['institution_ids'] == [2]:
            raise RuntimeError('query blew up')
        return {'data': _rows(1), 'total_count': 1}

    with patch('app.camera_traps.data_export.get_ct_occurrence_data',
               side_effect=side_effect):
        results = _run_backup(app, tmp_path, [good, bad])

    by_code = {r.code: r for r in results}
    assert not by_code['RSNR'].errors
    assert 'query blew up' in by_code['CNNP'].errors[0]
    assert (tmp_path / 'Roztochya_Nature_Reserve'
            / 'RSNR_ct_occurrence_2026-09-02.csv').is_file()


def test_run_backup_filters_by_institution_code(app, tmp_path):
    institutions = [_institution(1, 'RSNR', 'Roztochya Nature Reserve'),
                    _institution(2, 'CNNP', 'Carpathian NNP')]
    with patch('app.camera_traps.data_export.get_ct_occurrence_data',
               return_value={'data': _rows(1), 'total_count': 1}):
        results = _run_backup(app, tmp_path, institutions, only_codes=['cnnp'])
    assert [r.code for r in results] == ['CNNP']


def test_run_backup_refuses_to_run_without_a_backend(app):
    with app.app_context():
        with pytest.raises(RuntimeError, match='no storage backend'):
            ct_csv.run_backup({'KEEP_VERSIONS': 2, 'STORAGES': []})


def test_testing_config_has_no_storage_configured(app):
    """Guard: a test that forgets to patch must not write to the server root."""
    assert app.config['CT_CSV_BACKUP']['STORAGES'] == []
