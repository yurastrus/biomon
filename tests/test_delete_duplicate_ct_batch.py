"""Tests for the safety logic of scripts/delete_duplicate_ct_batch.py.

The script deletes photo rows, so the part worth pinning is the predicate that
decides whether a batch really is a duplicate. Two properties matter:

  * coverage is judged PER capture instant — a surplus at one second must
    never excuse a shortfall at another, or a burst would be silently
    truncated;
  * a 0-byte twin covers nothing, while an archived row does, because
    archiving deletes the file on purpose and the row is the surviving record.
"""

import importlib.util
import os
from types import SimpleNamespace

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'scripts', 'delete_duplicate_ct_batch.py')


@pytest.fixture(scope='module')
def mod():
    spec = importlib.util.spec_from_file_location('delete_duplicate_ct_batch',
                                                  SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def photo(instant, name='x.JPG', status='pending'):
    return SimpleNamespace(captured_at=instant, system_filename=name,
                           status=status)


def all_usable(_row):
    return True


def test_full_coverage_leaves_no_gap(mod):
    victims = [photo(1), photo(1), photo(2)]
    others = [photo(1), photo(1), photo(1), photo(2)]
    assert mod.find_coverage_gaps(victims, others, all_usable) == []


def test_shortfall_at_one_instant_is_reported(mod):
    victims = [photo(1), photo(1), photo(1)]
    others = [photo(1), photo(1)]
    assert mod.find_coverage_gaps(victims, others, all_usable) == [(1, 3, 2)]


def test_surplus_elsewhere_does_not_excuse_a_shortfall(mod):
    """The aggregate would look fine (4 others vs 3 victims) — it is not."""
    victims = [photo(1), photo(1), photo(2)]
    others = [photo(1), photo(2), photo(2), photo(2)]
    assert mod.find_coverage_gaps(victims, others, all_usable) == [(1, 2, 1)]


def test_instant_absent_from_others_is_a_gap(mod):
    victims = [photo(7)]
    assert mod.find_coverage_gaps(victims, [photo(1)], all_usable) == [(7, 1, 0)]


def test_unusable_counterparts_do_not_count(mod):
    """A 0-byte twin is not a copy of anything."""
    victims = [photo(1), photo(1)]
    others = [photo(1, 'good.JPG'), photo(1, 'empty.JPG')]

    def usable(row):
        return row.system_filename != 'empty.JPG'

    assert mod.find_coverage_gaps(victims, others, usable) == [(1, 2, 1)]


def test_gaps_are_sorted_by_instant(mod):
    victims = [photo(5), photo(2), photo(9)]
    gaps = mod.find_coverage_gaps(victims, [], all_usable)
    assert [g[0] for g in gaps] == [2, 5, 9]


def test_archived_row_counts_even_without_a_file(mod, tmp_path):
    row = photo(1, 'gone.JPG', status='archived')
    assert mod.thumbnail_is_usable(row, str(tmp_path)) is True


def test_missing_file_of_a_live_row_does_not_count(mod, tmp_path):
    row = photo(1, 'gone.JPG')
    assert mod.thumbnail_is_usable(row, str(tmp_path)) is False


def test_zero_byte_file_does_not_count(mod, tmp_path):
    (tmp_path / 'empty.JPG').write_bytes(b'')
    row = photo(1, 'empty.JPG')
    assert mod.thumbnail_is_usable(row, str(tmp_path)) is False


def test_real_file_counts(mod, tmp_path):
    (tmp_path / 'good.JPG').write_bytes(b'JPEGDATA')
    row = photo(1, 'good.JPG')
    assert mod.thumbnail_is_usable(row, str(tmp_path)) is True
