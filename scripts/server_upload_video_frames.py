# SPDX-License-Identifier: AGPL-3.0-only
"""
Upload frames already cut from camera-trap videos into one CT location from the
SERVER, the same way /upload-video stores them: one batch, process_single_photo
with captured_at_override per frame, then SQL grouping into series.

The frames are cut beforehand (ffmpeg, one per second, like the page) and come
with a manifest CSV: `file,captured_at` (file relative to the folder, captured_at
ISO local camera time, already corrected). The batch gets time_offset_seconds=0:
the override is the final time, exactly as for frames sent by the page.

Run on the server from the app root (it reads that .env, i.e. PROD):
    cd /var/www/biomon
    venv/bin/python ~/server_upload_video_frames.py --location 21 --user 1 ~/tmp_frames_k26      # dry run
    ... --apply                                                                                 # writes
Options: --save-originals (default: thumbnails only, like the page default).
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.getcwd())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--location', type=int, required=True)
    ap.add_argument('--user', type=int, required=True, help='main-DB user id recorded as uploader')
    ap.add_argument('--manifest', default='manifest.csv', help='CSV inside the folder: file,captured_at')
    ap.add_argument('--save-originals', action='store_true')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    folder = os.path.expanduser(args.folder)
    with open(os.path.join(folder, args.manifest), encoding='utf-8') as fh:
        rows = [(os.path.join(folder, r['file']), datetime.fromisoformat(r['captured_at']))
                for r in csv.DictReader(fh)]
    missing = [p for p, _ in rows if not os.path.exists(p)]
    rows.sort(key=lambda r: (r[1], r[0]))
    print(f'{len(rows)} frames, {len(missing)} missing, location={args.location}, '
          f'originals={args.save_originals}, {rows[0][1]} .. {rows[-1][1]}' if rows else 'no frames')
    if missing:
        print('missing:', missing[:10]); return
    if not args.apply:
        print('DRY RUN. Add --apply.'); return

    from werkzeug.datastructures import FileStorage
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.camera_traps.utils import create_upload_batch, process_single_photo
        from app.camera_traps.database import close_ct_session
        from app.camera_traps.fast_upload import _run_grouping_in_thread, _set_batch_status

        batch_id = create_upload_batch(args.location, args.user, len(rows), time_offset_seconds=0)
        close_ct_session()
        print('batch', batch_id, flush=True)
        ok, skipped, failed = 0, [], []
        t0 = time.time()
        for i, (path, taken) in enumerate(rows, 1):
            try:
                with open(path, 'rb') as fh:
                    fs = FileStorage(stream=fh, filename=os.path.basename(path), content_type='image/jpeg')
                    process_single_photo(fs, args.location, args.user, batch_id,
                                         save_original=args.save_originals,
                                         captured_at_override=taken)
                ok += 1
            except ValueError as e:      # duplicates and validation, same as the route's 400
                skipped.append((path, str(e)))
            except Exception as e:
                failed.append((path, repr(e)))
            finally:
                close_ct_session()
            if i % 200 == 0:
                print(f'  {i}/{len(rows)} ok={ok} skipped={len(skipped)} failed={len(failed)} '
                      f'{time.time() - t0:.0f}s', flush=True)
        print(f'uploaded ok={ok} skipped={len(skipped)} failed={len(failed)}')
        for p, e in skipped + failed:
            print('  !', p, e)
        if failed:
            _set_batch_status(batch_id, 'failed', f'{len(failed)} frames failed in server_upload_video_frames')
            print('NOT grouped: fix failures, then group via finalize.')
            return
        _run_grouping_in_thread(app, batch_id)
        print('grouped; batch', batch_id)


if __name__ == '__main__':
    main()
