"""Tests for the source-tree reasoning in scripts/restore_broken_ct_photos.py.

The matching itself is DB- and EXIF-bound and is exercised by the dry run
against the real export. What is worth pinning here is `camera_folder`,
because the whole "did we mix up two parks" guarantee rests on it: it has to
collapse DCIM buckets and dated dumps onto one camera, and must NOT collapse
two different cameras onto one folder — that would let the bijection check
pass while frames from camera 1901 were written under camera 1904's names.
"""

import importlib.util
import os

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'scripts', 'restore_broken_ct_photos.py')
ROOT = 'F:/photos'


@pytest.fixture(scope='module')
def mod():
    spec = importlib.util.spec_from_file_location('restore_broken_ct_photos', SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cf(mod, rel):
    return mod.camera_folder(os.path.join(ROOT, *rel.split('/')), ROOT)


def test_dcim_bucket_is_collapsed(mod):
    """Cheremoskyi: the camera folder is named directly under the upload dir."""
    base = 'park/proj/01_Original_Upload/DCIM Сосни 1601'
    assert cf(mod, base + '/100CUDDY/I_1.JPG') == base
    assert cf(mod, base + '/200CUDDY/I_2.JPG') == base


def test_dated_dump_is_collapsed_but_camera_number_survives(mod):
    """Synevyr: camera under a forestry district, with dated subdumps."""
    base = 'park/proj/01_Original_Upload/Остріцьке ПНДВ/1904'
    assert cf(mod, base + '/200CUDDY/01_01_25/I_3.JPG') == base
    assert cf(mod, base + '/200CUDDY/I_4.JPG') == base


def test_two_cameras_stay_distinct(mod):
    """The failure mode this guards: 1901 and 1904 must not collapse together."""
    a = cf(mod, 'park/proj/01_Original_Upload/Колочавське ПНДВ/1901/100CUDDY/x.JPG')
    b = cf(mod, 'park/proj/01_Original_Upload/Остріцьке ПНДВ/1904/100CUDDY/x.JPG')
    assert a != b
    assert a.endswith('1901') and b.endswith('1904')


def test_camera_directly_under_marker_is_never_stripped_away(mod):
    """A numeric camera folder sitting right under the marker must survive."""
    assert cf(mod, 'park/proj/01_Original_Upload/1820/DCIM/x.JPG') == \
        'park/proj/01_Original_Upload/1820'


def test_no_upload_marker_falls_back_to_the_directory(mod):
    """Unknown layout: be stricter, not looser — keep the full directory."""
    assert cf(mod, 'loose/folder/x.JPG') == 'loose/folder'
