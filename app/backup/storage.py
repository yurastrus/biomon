# SPDX-License-Identifier: AGPL-3.0-only
"""Pluggable storage backends for the CSV backup layer.

A backend receives already-rendered files and is responsible for placing them
somewhere durable, plus for keeping only the N newest versions of each logical
file. Everything the exporter needs is expressed by four methods, so adding
Nextcloud later means writing one class here and naming it in the config — no
change to the exporter.

Backends currently implemented:

    LocalStorage   — a directory on the server (next to the SQL dumps).
    RcloneStorage  — any rclone remote (``gdrive_backup:``, a future
                     ``nextcloud:``), driven by the rclone binary already
                     installed for the dump sync.

Path model
----------
Every file is addressed by ``(folder, filename)``: ``folder`` is the
institution directory (English name, slugified), ``filename`` carries the
institution code and the date. A backend maps that pair onto its own namespace
however it likes; the exporter never builds absolute paths itself.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """A backend could not complete an operation. Never fatal to the run."""


class StorageBackend(ABC):
    """Destination for exported CSV files.

    Implementations must be safe to call repeatedly: the exporter runs nightly
    and may be re-run by hand on the same day.
    """

    #: short name used in config and log lines
    name = 'abstract'

    @abstractmethod
    def put(self, folder: str, filename: str, data: bytes) -> str:
        """Store ``data`` as ``folder/filename``; return a human-readable location."""

    @abstractmethod
    def read_manifest(self, folder: str) -> dict:
        """Return the manifest dict for ``folder`` ({} when absent or unreadable)."""

    @abstractmethod
    def write_manifest(self, folder: str, manifest: dict) -> None:
        """Persist the manifest for ``folder``."""

    @abstractmethod
    def rotate(self, folder: str, pattern: str, keep: int) -> list:
        """Delete all but the ``keep`` newest files matching ``pattern``.

        Returns the list of removed file names. ``pattern`` is a glob such as
        ``RSNR_ct_occurrence_*.csv``; matching is by name, ordering by name
        (the date in the filename sorts chronologically because it is ISO).
        """


MANIFEST_NAME = 'manifest.json'


class LocalStorage(StorageBackend):
    """Plain directory on the server — the tier that must never fail.

    ``root`` is created on demand. This is the backend that writes next to the
    PostgreSQL and GeoServer dumps, so the existing ``rclone sync`` of the whole
    backup root carries the CSVs to Google Drive without any extra upload.
    """

    name = 'local'

    def __init__(self, root):
        self.root = Path(root)

    def _dir(self, folder: str) -> Path:
        path = self.root / folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    def put(self, folder: str, filename: str, data: bytes) -> str:
        target = self._dir(folder) / filename
        # Write via a temp file in the same directory, then replace: a crash
        # mid-write must never leave a truncated CSV that looks like a backup.
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(data)
            os.replace(tmp, target)
        except Exception:
            _silent_unlink(tmp)
            raise
        return str(target)

    def read_manifest(self, folder: str) -> dict:
        path = self.root / folder / MANIFEST_NAME
        if not path.is_file():
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError) as exc:
            # A corrupt manifest must not block the export — it only means the
            # change check cannot short-circuit, so we re-export this run.
            logger.warning('[ct-csv-backup] unreadable manifest %s: %s', path, exc)
            return {}

    def write_manifest(self, folder: str, manifest: dict) -> None:
        blob = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        self.put(folder, MANIFEST_NAME, blob.encode('utf-8'))

    def rotate(self, folder: str, pattern: str, keep: int) -> list:
        directory = self.root / folder
        if not directory.is_dir():
            return []
        files = sorted((p for p in directory.glob(pattern) if p.is_file()),
                       key=lambda p: p.name, reverse=True)
        removed = []
        for path in files[max(keep, 1):]:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError as exc:
                logger.warning('[ct-csv-backup] cannot delete %s: %s', path, exc)
        return removed


class RcloneStorage(StorageBackend):
    """Any rclone remote: Google Drive today, Nextcloud when it arrives.

    Only needed when the CSVs must reach a destination the existing
    ``rclone sync`` of the backup root does not already cover — a separate
    Drive folder, or a different provider. Configure with::

        {'type': 'rclone', 'remote': 'gdrive_backup:biomon/phototraps_data',
         'config': '/home/yura/.config/rclone/rclone.conf'}

    Failures are raised as StorageError and downgraded to a warning by the
    exporter: a cloud copy that did not happen must not lose the local one.
    """

    name = 'rclone'

    def __init__(self, remote, config_path=None, binary='rclone', timeout=600):
        self.remote = remote.rstrip('/')
        self.config_path = config_path
        self.binary = binary
        self.timeout = timeout

    def _run(self, args, stdin_data=None):
        cmd = [self.binary]
        if self.config_path:
            cmd += ['--config', self.config_path]
        cmd += args
        try:
            proc = subprocess.run(
                cmd, input=stdin_data, capture_output=True, timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StorageError(f'rclone {" ".join(args)} failed: {exc}') from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or b'').decode('utf-8', 'replace').strip()
            raise StorageError(f'rclone {" ".join(args)} exited {proc.returncode}: {stderr}')
        return proc.stdout or b''

    def put(self, folder: str, filename: str, data: bytes) -> str:
        dest = f'{self.remote}/{folder}/{filename}'
        # `rclone rcat` streams stdin straight to the remote — no temp file on a
        # disk that is already at 96% use.
        self._run(['rcat', dest], stdin_data=data)
        return dest

    def read_manifest(self, folder: str) -> dict:
        try:
            raw = self._run(['cat', f'{self.remote}/{folder}/{MANIFEST_NAME}'])
        except StorageError:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}

    def write_manifest(self, folder: str, manifest: dict) -> None:
        blob = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        self.put(folder, MANIFEST_NAME, blob.encode('utf-8'))

    def rotate(self, folder: str, pattern: str, keep: int) -> list:
        try:
            raw = self._run(['lsjson', f'{self.remote}/{folder}'])
            entries = json.loads(raw.decode('utf-8'))
        except (StorageError, ValueError, UnicodeDecodeError) as exc:
            logger.warning('[ct-csv-backup] rclone listing of %s/%s failed: %s',
                           self.remote, folder, exc)
            return []
        regex = re.compile(_glob_to_regex(pattern))
        names = sorted((e['Name'] for e in entries
                        if not e.get('IsDir') and regex.fullmatch(e.get('Name', ''))),
                       reverse=True)
        removed = []
        for name in names[max(keep, 1):]:
            try:
                self._run(['deletefile', f'{self.remote}/{folder}/{name}'])
                removed.append(name)
            except StorageError as exc:
                logger.warning('[ct-csv-backup] cannot delete %s: %s', name, exc)
        return removed


_BACKEND_TYPES = {
    'local': lambda cfg: LocalStorage(cfg['root']),
    'rclone': lambda cfg: RcloneStorage(
        cfg['remote'], cfg.get('config'), cfg.get('binary', 'rclone'),
        cfg.get('timeout', 600),
    ),
}


def build_backends(specs):
    """Instantiate the backends described by a list of config dicts.

    Each spec is ``{'type': 'local'|'rclone', 'enabled': bool, ...}``. Disabled
    and unknown types are skipped with a warning rather than raising, so a typo
    in the config cannot take the nightly backup down entirely.
    """
    backends = []
    for spec in specs or []:
        if not spec.get('enabled', True):
            continue
        kind = spec.get('type')
        factory = _BACKEND_TYPES.get(kind)
        if factory is None:
            logger.warning('[ct-csv-backup] unknown storage type %r, skipped', kind)
            continue
        try:
            backends.append(factory(spec))
        except (KeyError, TypeError) as exc:
            logger.warning('[ct-csv-backup] bad config for storage %r: %s', kind, exc)
    return backends


_SLUG_RE = re.compile(r'[^A-Za-z0-9]+')


def slugify_folder(name, fallback='unknown'):
    """Turn an institution name into a safe directory name.

    ASCII only and no path separators: the folder is created on the server, in
    Google Drive, and possibly on Nextcloud, and every one of those has its own
    opinion about non-ASCII and about ``/``. Underscores keep it readable.
    """
    slug = _SLUG_RE.sub('_', (name or '').strip()).strip('_')
    return slug or fallback


def _glob_to_regex(pattern):
    """Translate the tiny glob subset we use (``*``) into a regex."""
    return '.*'.join(re.escape(part) for part in pattern.split('*'))


def _silent_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# Kept for callers that want to drop a whole directory (tests, manual cleanup).
def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
