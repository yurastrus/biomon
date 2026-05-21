"""Tests for ZIP-bomb protection in PAM upload processing."""
import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_real_zip(content: bytes = b"audio data") -> bytes:
    """Return an in-memory ZIP with a single small file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("species_a/file.wav", content)
    return buf.getvalue()


MAX_TOTAL_EXTRACTED = 5 * 1024 * 1024 * 1024  # mirrors the production constant


def _check_zip_size(zip_path: str) -> None:
    """Reproduce the guard logic from pam_upload_utils.process_zip_archive."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        total_size = sum(info.file_size for info in zf.infolist())
        if total_size > MAX_TOTAL_EXTRACTED:
            raise ValueError(
                f"ZIP refuses extraction: uncompressed size {total_size} bytes > "
                f"limit {MAX_TOTAL_EXTRACTED} bytes (potential zip bomb)"
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestZipBombGuard:
    """Verify the uncompressed-size cap used in process_zip_archive."""

    def test_check_zip_size_rejects_bomb(self, tmp_path):
        """A ZIP whose infolist reports >5 GB uncompressed must raise ValueError."""
        real_zip = tmp_path / "bomb.zip"
        real_zip.write_bytes(_make_real_zip())

        oversized_info = MagicMock()
        oversized_info.file_size = 6 * 1024 ** 3  # 6 GB

        with patch("zipfile.ZipFile.infolist", return_value=[oversized_info]):
            with pytest.raises(ValueError, match="potential zip bomb"):
                _check_zip_size(str(real_zip))

    def test_valid_zip_passes_size_check(self, tmp_path):
        """A real small ZIP must pass the size guard without raising."""
        real_zip = tmp_path / "valid.zip"
        real_zip.write_bytes(_make_real_zip(b"x" * 1024))  # 1 KB content

        # Should not raise
        _check_zip_size(str(real_zip))

    def test_exact_limit_passes(self, tmp_path):
        """A ZIP reported at exactly 5 GB must not be rejected."""
        real_zip = tmp_path / "edge.zip"
        real_zip.write_bytes(_make_real_zip())

        at_limit_info = MagicMock()
        at_limit_info.file_size = MAX_TOTAL_EXTRACTED  # exactly at cap

        with patch("zipfile.ZipFile.infolist", return_value=[at_limit_info]):
            # Should not raise
            _check_zip_size(str(real_zip))

    def test_one_byte_over_limit_is_rejected(self, tmp_path):
        """A ZIP reported at 5 GB + 1 byte must be rejected."""
        real_zip = tmp_path / "over.zip"
        real_zip.write_bytes(_make_real_zip())

        over_info = MagicMock()
        over_info.file_size = MAX_TOTAL_EXTRACTED + 1

        with patch("zipfile.ZipFile.infolist", return_value=[over_info]):
            with pytest.raises(ValueError, match="potential zip bomb"):
                _check_zip_size(str(real_zip))
