# SPDX-License-Identifier: AGPL-3.0-only
"""
Upload a folder of camera-trap photos into one CT location from the SERVER,
through the same code path as /upload-fast (create_upload_batch ->
process_single_photo per file -> SQL grouping into series).

Use it when the photos are already on the server (scp/tar) and pushing them
through the browser is impractical. The batch gets the same clock correction
(time_offset_seconds) the page would set, so captured_at is shifted exactly
as in the UI and the original stays recoverable as captured_at - offset.

Run on the server from the app root (it reads that .env, i.e. PROD):
    cd /var/www/biomon
    venv/bin/python ~/server_upload_folder.py --location 3216 --user 1 \
        --offset -15249600 ~/tmp_upload_2008              # dry run: lists files
    ... --apply                                           # writes
Options: --save-originals (default: thumbnails only, like the page default).
Files are walked recursively (*.jpg/*.jpeg), sorted by relative path.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.getcwd())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--location', type=int, required=True)
    ap.add_argument('--user', type=int, required=True, help='main-DB user id recorded as uploader')
    ap.add_argument('--offset', type=int, default=0, help='clock correction, seconds')
    ap.add_argument('--save-originals', action='store_true')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    files = []
    for dp, _dn, fn in os.walk(os.path.expanduser(args.folder)):
        for f in fn:
            if f.lower().endswith(('.jpg', '.jpeg')):
                files.append(os.path.join(dp, f))
    files.sort()
    print(f'{len(files)} files, location={args.location}, offset={args.offset}s, '
          f'originals={args.save_originals}')
    if not args.apply:
        print('DRY RUN. Add --apply.')
        return

    from werkzeug.datastructures import FileStorage
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.camera_traps.utils import create_upload_batch, process_single_photo, parse_time_offset
        from app.camera_traps.database import close_ct_session
        from app.camera_traps.fast_upload import _run_grouping_in_thread, _set_batch_status

        batch_id = create_upload_batch(args.location, args.user, len(files),
                                       time_offset_seconds=parse_time_offset(args.offset))
        close_ct_session()
        print('batch', batch_id, flush=True)
        ok, skipped, failed = 0, [], []
        t0 = time.time()
        for i, path in enumerate(files, 1):
            try:
                with open(path, 'rb') as fh:
                    fs = FileStorage(stream=fh, filename=os.path.basename(path),
                                     content_type='image/jpeg')
                    process_single_photo(fs, args.location, args.user, batch_id,
                                         save_original=args.save_originals)
                ok += 1
            except ValueError as e:      # duplicates and validation, same as the route's 400
                skipped.append((path, str(e)))
            except Exception as e:
                failed.append((path, repr(e)))
            finally:
                close_ct_session()
            if i % 100 == 0:
                print(f'  {i}/{len(files)} ok={ok} skipped={len(skipped)} failed={len(failed)} '
                      f'{time.time() - t0:.0f}s', flush=True)
        print(f'uploaded ok={ok} skipped={len(skipped)} failed={len(failed)}')
        for p, e in skipped + failed:
            print('  !', p, e)
        if failed:
            _set_batch_status(batch_id, 'failed', f'{len(failed)} files failed in server_upload_folder')
            print('NOT grouped: fix failures, then group via finalize.')
            return
        _run_grouping_in_thread(app, batch_id)   # synchronous here: grouping + status
        print('grouped; batch', batch_id)


if __name__ == '__main__':
    main()
