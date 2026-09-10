"""Tests for the pure helpers of scripts/audit_broken_ct_photos.py.

The DB/repair path needs prod data and is exercised manually; what is worth
pinning here is the manifest parsing, because the whole audit hinges on it:
a filename containing a space must not be truncated, and a zero size must
survive as 0 rather than becoming falsy-by-accident somewhere else.
"""

import gzip
import importlib.util
import os

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'scripts', 'audit_broken_ct_photos.py')


@pytest.fixture(scope='module')
def mod():
    spec = importlib.util.spec_from_file_location('audit_broken_ct_photos', SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _write(path, lines):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


def test_load_manifest_plain(mod, tmp_path):
    p = tmp_path / 'thumbs.txt'
    _write(p, ['12345 a.JPG', '0 b.JPG', '7 name with spaces.JPG'])
    sizes = mod.load_manifest(str(p))
    assert sizes == {'a.JPG': 12345, 'b.JPG': 0, 'name with spaces.JPG': 7}


def test_load_manifest_gzip_and_blank_lines(mod, tmp_path):
    p = tmp_path / 'thumbs.gz'
    with gzip.open(p, 'wt', encoding='utf-8') as fh:
        fh.write('5 a.JPG\n\n0 b.JPG\n')
    assert mod.load_manifest(str(p)) == {'a.JPG': 5, 'b.JPG': 0}


def test_zero_size_is_kept_distinct_from_missing(mod, tmp_path):
    """A 0-byte entry must be present-with-size-0, not absent.

    The audit distinguishes 'zero' (file exists, delete it) from 'missing'
    (nothing to delete) purely by `sizes.get(name) is None`.
    """
    p = tmp_path / 'thumbs.txt'
    _write(p, ['0 empty.JPG'])
    sizes = mod.load_manifest(str(p))
    assert sizes.get('empty.JPG') == 0
    assert sizes.get('absent.JPG') is None


def test_scan_dir_reports_sizes(mod, tmp_path):
    (tmp_path / 'good.JPG').write_bytes(b'xxxx')
    (tmp_path / 'empty.JPG').write_bytes(b'')
    (tmp_path / 'sub').mkdir()
    sizes = mod.scan_dir(str(tmp_path))
    assert sizes == {'good.JPG': 4, 'empty.JPG': 0}
