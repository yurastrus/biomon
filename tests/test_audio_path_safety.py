"""
SEC Phase 3 (#26, Option A): confinement of PAM audio/spectrogram paths.

serve_verification_audio / serve_spectrogram_image used to serve a file by its
absolute file_path from the DB without validation. _confine_to_pam_base() ensures
the path lies inside PAM_UPLOAD_PATH; otherwise 403. Guards against a file_path
tampered with in the DB.
"""
import os

import pytest
from werkzeug.exceptions import Forbidden


def test_path_inside_base_allowed(app, tmp_path, monkeypatch):
    from app.pam.routes import _confine_to_pam_base
    sub = tmp_path / 'sub'
    sub.mkdir()
    f = sub / 'segment.wav'
    f.write_bytes(b'RIFF')
    with app.app_context():
        monkeypatch.setitem(app.config, 'PAM_UPLOAD_PATH', str(tmp_path))
        assert _confine_to_pam_base(str(f)) == os.path.realpath(str(f))


def test_path_outside_base_aborts_403(app, tmp_path, monkeypatch):
    from app.pam.routes import _confine_to_pam_base
    base = tmp_path / 'allowed'
    base.mkdir()
    outside = tmp_path / 'secret.txt'      # sibling directory, outside base
    outside.write_bytes(b'secret')
    with app.app_context():
        monkeypatch.setitem(app.config, 'PAM_UPLOAD_PATH', str(base))
        with pytest.raises(Forbidden):
            _confine_to_pam_base(str(outside))


def test_traversal_escape_aborts_403(app, tmp_path, monkeypatch):
    from app.pam.routes import _confine_to_pam_base
    base = tmp_path / 'allowed'
    base.mkdir()
    evil = str(base / '..' / '..' / 'etc' / 'passwd')
    with app.app_context():
        monkeypatch.setitem(app.config, 'PAM_UPLOAD_PATH', str(base))
        with pytest.raises(Forbidden):
            _confine_to_pam_base(evil)


def test_unconfigured_base_skips_without_break(app, tmp_path, monkeypatch):
    """If PAM_UPLOAD_PATH is not configured, returns the path without 403 (no regression)."""
    from app.pam.routes import _confine_to_pam_base
    p = str(tmp_path / 'x.wav')
    with app.app_context():
        monkeypatch.setitem(app.config, 'PAM_UPLOAD_PATH', None)
        assert _confine_to_pam_base(p) == p
