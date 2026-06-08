"""
SEC Phase 3 (#26, Варіант A): confinement шляхів до аудіо/спектрограм PAM.

serve_verification_audio / serve_spectrogram_image віддавали файл за абсолютним
file_path з БД без перевірки. _confine_to_pam_base() гарантує, що шлях лежить
усередині PAM_UPLOAD_PATH; інакше — 403. Захист від підміненого в БД file_path.
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
    outside = tmp_path / 'secret.txt'      # сусідня тека, поза base
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
    """Якщо PAM_UPLOAD_PATH не налаштовано — повертає шлях без 403 (без регресії)."""
    from app.pam.routes import _confine_to_pam_base
    p = str(tmp_path / 'x.wav')
    with app.app_context():
        monkeypatch.setitem(app.config, 'PAM_UPLOAD_PATH', None)
        assert _confine_to_pam_base(p) == p
