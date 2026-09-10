"""Install thumbnails rebuilt by scripts/restore_broken_ct_photos.py.

Runs ON THE SERVER, where the photo volume lives. It is the only step that
touches production files, so it is written to be boring and refusable:

  * a file is installed only if its name is a `system_filename` known to the
    DB — an unknown name means the rebuild and the DB have drifted apart;
  * a file is installed only if the target is missing or exactly 0 bytes.
    A healthy thumbnail is never overwritten, whatever the source directory
    happens to contain;
  * the source must itself be a non-empty readable JPEG;
  * the write goes to a temporary name in the same directory and is renamed
    into place, so a failure mid-copy cannot leave a new 0-byte file — that
    being the exact defect we are repairing.

Ownership and mode are set to match the neighbouring thumbnails
(yura:www-data, 0644) so the gunicorn workers can read them.

With --requeue-ai the bogus AI predictions of the installed photos are
deleted. Those rows all say `empty` with score 1.0, which is what the
classifier returns for a 0-byte file; the worker picks up observations that
have no prediction, so deleting them puts the restored series back in the
queue and they get classified against the real image.

Usage
-----
    scp -r restored/ yurastrus.dev:/tmp/restored_thumbs
    ssh yurastrus.dev
    cd /var/www/biomon
    venv/bin/python deploy/install_restored_thumbs.py /tmp/restored_thumbs
    venv/bin/python deploy/install_restored_thumbs.py /tmp/restored_thumbs --apply
    venv/bin/python deploy/install_restored_thumbs.py /tmp/restored_thumbs --apply --requeue-ai
"""

from __future__ import annotations

import argparse
import grp
import os
import pwd
import shutil
import sys
from collections import Counter

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.camera_traps.database import get_ct_engine  # noqa: E402

OWNER = 'yura'
GROUP = 'www-data'
MODE = 0o644


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', help='directory holding the rebuilt thumbnails')
    ap.add_argument('--apply', action='store_true',
                    help='actually install (default: report what would happen)')
    ap.add_argument('--requeue-ai', action='store_true',
                    help='delete the bogus `empty` AI predictions of the '
                         'installed photos so the worker reclassifies them')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        config = app.config['CAMERA_TRAP_CONFIG']
        thumb_dir = os.path.join(config['UPLOAD_PATH'], 'pending_photos', 'thumbnails')
        if not os.path.isdir(thumb_dir):
            print('not the server: %s does not exist' % thumb_dir, file=sys.stderr)
            return 2

        names = sorted(f for f in os.listdir(args.source)
                       if os.path.isfile(os.path.join(args.source, f)))
        print('files offered: %d' % len(names))

        engine = get_ct_engine()
        with engine.connect() as conn:
            known = dict(conn.execute(text("""
                SELECT system_filename, id FROM photos
                 WHERE system_filename = ANY(:names)
            """), {'names': names}).fetchall())

        plan, skip = [], Counter()
        for name in names:
            src = os.path.join(args.source, name)
            dst = os.path.join(thumb_dir, name)
            if name not in known:
                skip['name unknown to the DB'] += 1
                continue
            if os.path.getsize(src) == 0:
                skip['source is empty'] += 1
                continue
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                skip['target already healthy'] += 1
                continue
            plan.append((src, dst, known[name]))

        print('to install: %d' % len(plan))
        for reason, n in skip.items():
            print('  skipped, %s: %d' % (reason, n))

        if not args.apply:
            print('\nreport only. Rerun with --apply to install.')
            return 0

        uid = pwd.getpwnam(OWNER).pw_uid
        gid = grp.getgrnam(GROUP).gr_gid
        installed, failed, photo_ids = 0, 0, []
        for src, dst, photo_id in plan:
            tmp = dst + '.incoming'
            try:
                shutil.copyfile(src, tmp)
                if os.path.getsize(tmp) == 0:
                    raise IOError('copy produced 0 bytes (disk full?)')
                os.chown(tmp, uid, gid)
                os.chmod(tmp, MODE)
                os.replace(tmp, dst)
                installed += 1
                photo_ids.append(photo_id)
            except OSError as exc:
                failed += 1
                print('  FAILED %s: %s' % (os.path.basename(dst), exc))
                if os.path.exists(tmp):
                    os.remove(tmp)
        print('\ninstalled: %d  failed: %d' % (installed, failed))

        if args.requeue_ai and photo_ids:
            with engine.begin() as conn:
                obs = [r[0] for r in conn.execute(text(
                    'SELECT DISTINCT observation_id FROM photos WHERE id = ANY(:ids)'),
                    {'ids': photo_ids}).fetchall()]
                n = conn.execute(text(
                    'DELETE FROM ai_predictions WHERE observation_id = ANY(:ids)'),
                    {'ids': obs}).rowcount
            print('deleted %d stale AI predictions across %d series — they will '
                  'be reclassified' % (n, len(obs)))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
