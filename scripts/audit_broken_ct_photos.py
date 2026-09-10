"""Audit (and optionally repair) camera-trap photo rows whose image file is
unusable: missing from disk, or present but zero bytes.

Background
----------
On 2026-07-02 and 2026-07-08 the photo volume ran out of space mid-upload.
process_single_photo created the DB rows and opened the thumbnail files, but
the writes produced 0-byte JPEGs. The raw originals were never written either,
and routine cleanup has since removed whatever raw files did exist. The result
is series that the /identify page lists but cannot display: the DB looks
healthy, the pixels are gone. The disk-full guard added on 2026-07-09 prevents
new occurrences; this script deals with the rows created before it.

Photos of ARCHIVED observations are expected to have no file, because
archiving deletes files on purpose (see background_tasks.archive_old_observations).
They are reported separately and never touched.

Usage
-----
On the server (reads the photo volume directly):

    venv/bin/python -m scripts.audit_broken_ct_photos            # report
    venv/bin/python -m scripts.audit_broken_ct_photos --delete   # repair

From a workstation over the SSH tunnel, pass a manifest produced on the server
with: find thumbnails -maxdepth 1 -type f -printf '%s %f\n'

    venv/Scripts/python -m scripts.audit_broken_ct_photos --manifest thumbs.txt

--delete removes, for each affected non-archived photo: its identifications
(and their behaviours), its AI predictions, the photo row, the zero-byte file,
and, when every photo of the observation is affected, the observation itself
together with its observation-level AI predictions. Observations that keep at
least one usable photo survive with a corrected photo_count.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.camera_traps.database import get_ct_engine  # noqa: E402


def load_manifest(path: str) -> dict:
    """Read a '<size> <filename>' listing (optionally gzipped)."""
    opener = gzip.open if path.endswith('.gz') else open
    sizes = {}
    with opener(path, 'rt', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line:
                continue
            size, name = line.split(' ', 1)
            sizes[name] = int(size)
    return sizes


def scan_dir(thumb_dir: str) -> dict:
    sizes = {}
    with os.scandir(thumb_dir) as it:
        for entry in it:
            if entry.is_file():
                sizes[entry.name] = entry.stat().st_size
    return sizes


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest',
                    help='file listing produced on the server with find -printf')
    ap.add_argument('--delete', action='store_true',
                    help='actually remove the broken rows (default: report only)')
    ap.add_argument('--csv', help='write the affected rows to this CSV')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        config = app.config['CAMERA_TRAP_CONFIG']
        thumb_dir = os.path.join(config['UPLOAD_PATH'], 'pending_photos', 'thumbnails')

        if args.manifest:
            sizes = load_manifest(args.manifest)
            source = 'manifest %s' % args.manifest
        else:
            if not os.path.isdir(thumb_dir):
                print('thumbnail directory not found: %s\n'
                      'Run on the server, or pass --manifest.' % thumb_dir,
                      file=sys.stderr)
                return 2
            sizes = scan_dir(thumb_dir)
            source = thumb_dir
        zero_on_disk = sum(1 for v in sizes.values() if v == 0)
        print('thumbnail files known from %s: %d (%d zero-byte)'
              % (source, len(sizes), zero_on_disk))

        engine = get_ct_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT p.id, p.observation_id, p.system_filename, p.status,
                       p.upload_batch_id, p.is_favorite,
                       o.location_id, o.status AS obs_status, o.created_at
                  FROM photos p
                  JOIN observations o ON o.id = p.observation_id
            """)).fetchall()
        print('photo rows with an observation: %d' % len(rows))

        archived, broken = [], []
        for r in rows:
            size = sizes.get(r.system_filename)
            if size:
                continue
            kind = 'missing' if size is None else 'zero'
            (archived if r.status == 'archived' else broken).append((r, kind))

        print('\narchived photos without a file (expected, ignored): %d' % len(archived))
        print('BROKEN photos (non-archived, no usable image): %d' % len(broken))
        if not broken:
            return 0

        print('  kinds        :', dict(Counter(k for _, k in broken)))
        print('  photo status :', dict(Counter(r.status for r, _ in broken)))
        print('  upload dates :', dict(Counter(str(r.created_at)[:10] for r, _ in broken)))
        print('  favorites    :', sum(1 for r, _ in broken if r.is_favorite))

        by_obs = defaultdict(list)
        for r, kind in broken:
            by_obs[r.observation_id].append((r, kind))

        with engine.connect() as conn:
            totals = dict(conn.execute(text("""
                SELECT observation_id, count(*) FROM photos
                 WHERE observation_id = ANY(:ids) GROUP BY observation_id
            """), {'ids': list(by_obs)}).fetchall())

        full = [o for o in by_obs if len(by_obs[o]) == totals[o]]
        partial = [o for o in by_obs if len(by_obs[o]) < totals[o]]
        print('  observations affected: %d (%d with no usable photo at all, '
              '%d partially)' % (len(by_obs), len(full), len(partial)))

        photo_ids = [r.id for r, _ in broken]
        with engine.connect() as conn:
            n_ident = conn.execute(text(
                'SELECT count(*) FROM identifications WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).scalar()
            n_ai = conn.execute(text(
                'SELECT count(*) FROM ai_predictions WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).scalar()
        print('  human identifications attached: %d' % n_ident)
        print('  AI predictions attached       : %d' % n_ai)

        if args.csv:
            with open(args.csv, 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(['photo_id', 'observation_id', 'system_filename', 'kind',
                            'photo_status', 'obs_status', 'location_id',
                            'upload_batch_id', 'created_at'])
                for r, kind in broken:
                    w.writerow([r.id, r.observation_id, r.system_filename, kind,
                                r.status, r.obs_status, r.location_id,
                                r.upload_batch_id, r.created_at])
            print('\nwrote %s' % args.csv)

        if not args.delete:
            print('\nreport only. Rerun with --delete to remove these rows.')
            return 0

        print('\ndeleting...')
        with engine.begin() as conn:
            d_beh = conn.execute(text("""
                DELETE FROM identification_behaviors
                 WHERE identification_id IN (
                       SELECT id FROM identifications WHERE photo_id = ANY(:ids))
            """), {'ids': photo_ids}).rowcount
            d_ident = conn.execute(text(
                'DELETE FROM identifications WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).rowcount
            d_ai = conn.execute(text(
                'DELETE FROM ai_predictions WHERE photo_id = ANY(:ids)'),
                {'ids': photo_ids}).rowcount
            d_photo = conn.execute(text(
                'DELETE FROM photos WHERE id = ANY(:ids)'),
                {'ids': photo_ids}).rowcount
            d_ai_obs = conn.execute(text(
                'DELETE FROM ai_predictions WHERE observation_id = ANY(:ids)'),
                {'ids': full}).rowcount
            d_obs = conn.execute(text(
                'DELETE FROM observations WHERE id = ANY(:ids)'),
                {'ids': full}).rowcount
            fixed = conn.execute(text("""
                UPDATE observations o
                   SET photo_count = (SELECT count(*) FROM photos p
                                       WHERE p.observation_id = o.id)
                 WHERE o.id = ANY(:ids)
            """), {'ids': partial}).rowcount

        removed = 0
        for r, kind in broken:
            if kind != 'zero':
                continue
            path = os.path.join(thumb_dir, r.system_filename)
            try:
                if os.path.isfile(path) and os.path.getsize(path) == 0:
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                print('  could not remove %s: %s' % (path, exc))

        print('  identification_behaviors : %d' % d_beh)
        print('  identifications          : %d' % d_ident)
        print('  ai_predictions (photo)   : %d' % d_ai)
        print('  ai_predictions (series)  : %d' % d_ai_obs)
        print('  photos                   : %d' % d_photo)
        print('  observations             : %d' % d_obs)
        print('  observations recounted   : %d' % fixed)
        print('  zero-byte files removed  : %d' % removed)
        print('\ndone at %s. Recalculate CT analytics afterwards.'
              % datetime.now(timezone.utc).isoformat(timespec='seconds'))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
