# SPDX-License-Identifier: AGPL-3.0-only
"""
Dry run of the /upload-fast time shift on a local folder. Touches no database.

Reads every JPEG's capture time with the uploader's own
`extract_datetime_from_exif` (same plausibility guard), applies the offset the
way process_single_photo does, and prints "was -> will be" plus a summary.
Use it to check a shift on real photos before uploading them.

    venv/Scripts/python -m scripts.preview_time_shift "F:/photos/folder" -3600
    venv/Scripts/python -m scripts.preview_time_shift "F:/photos/folder" -3600 --all
"""
import argparse
import os
from datetime import timedelta

from flask import Flask

from app.camera_traps.utils import extract_datetime_from_exif, parse_time_offset


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('folder')
    ap.add_argument('offset', help='seconds, e.g. -3600')
    ap.add_argument('--all', action='store_true', help='print every file, not a sample')
    args = ap.parse_args()

    offset = parse_time_offset(args.offset)
    delta = timedelta(seconds=offset) if offset else None

    files = []
    for root, _, names in os.walk(args.folder):
        files += [os.path.join(root, n) for n in names if n.lower().endswith(('.jpg', '.jpeg'))]
    files.sort()

    rows, no_exif, refused = [], 0, 0
    # A bare Flask app: the guard logs through current_app.
    with Flask(__name__).app_context():
        for path in files:
            with open(path, 'rb') as fh:
                before = extract_datetime_from_exif(fh)
                after = extract_datetime_from_exif(fh, offset=delta)
            if before is None and after is None:
                no_exif += 1
            elif after is None:
                refused += 1
            rows.append((os.path.relpath(path, args.folder), before, after))

    shown = rows if args.all or len(rows) <= 10 else rows[:5] + [None] + rows[-5:]
    for r in shown:
        if r is None:
            print('  ...')
            continue
        name, before, after = r
        print(f'  {name:<40} {before!s:<26} -> {after!s}')

    shifted = [r for r in rows if r[1] is not None and r[2] is not None]
    wrong = [r for r in shifted if r[2] - r[1] != (delta or timedelta(0))]
    print()
    print(f'files: {len(rows)}  offset: {offset:+d} s  no EXIF: {no_exif}  '
          f'refused after shift: {refused}  mismatched: {len(wrong)}')
    if shifted:
        print(f'range before: {min(r[1] for r in shifted)} .. {max(r[1] for r in shifted)}')
        print(f'range after : {min(r[2] for r in shifted)} .. {max(r[2] for r in shifted)}')


if __name__ == '__main__':
    main()
