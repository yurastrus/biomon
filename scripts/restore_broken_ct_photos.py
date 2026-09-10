"""Rebuild the thumbnails of camera-trap photos whose files were lost to the
2026-07 disk-full incident, from the originals supplied by the parks.

See scripts/audit_broken_ct_photos.py for how the damage was established. The
DB rows are intact and keep their `system_filename`; only the JPEG behind each
row is gone (0 bytes, or absent). So this is a file-level repair: no row is
inserted, renamed or re-dated. For every broken row we look for the original
frame, regenerate the thumbnail exactly as an upload would have, and write it
under the name the DB already expects.

Matching is deliberately paranoid, because a wrong file under a right name is
worse than a missing file. A source frame is accepted for a row only when

  * its basename equals the row's `original_filename`, AND
  * its EXIF DateTimeOriginal (sub-seconds included, read with the very same
    `extract_datetime_from_exif` the uploader uses) equals the row's
    `captured_at` to the microsecond.

Everything else is a cross-check that can only reject, never match:

  * folder ↔ location must be a bijection. If one camera folder feeds two
    locations, or one location is fed by two folders, every row involved is
    refused — that is exactly the "переплутати парки" failure mode.
  * a series is restored only when ALL of its broken photos are matched.
    A half-restored series would silently change what the verifier sees
    (a roe deer walking out of frame in the missing half). Use --partial to
    allow them anyway.
  * the same source file may not serve two different rows.

The thumbnail is produced the way the server produces one: PIL `thumbnail()`
to CAMERA_TRAP_CONFIG['THUMBNAIL_SIZE'], JPEG quality 85. The uploader's fast
path normally saves the browser-compressed file, which keeps EXIF, so the EXIF
block is copied over too — that is what the healthy neighbours on disk look
like.

Nothing is written to the production volume by this script. It renders into a
local directory; `deploy/install_restored_thumbs.py`, run on the server,
installs them and refuses to touch any file that is not missing or 0 bytes.

Usage
-----
    # dry run: match only, write nothing
    venv/Scripts/python -m scripts.restore_broken_ct_photos \
        --source "F:/20_Phototraps_photos/4 - FZS" \
        --manifest thumbs.gz

    # render the thumbnails that matched
    venv/Scripts/python -m scripts.restore_broken_ct_photos \
        --source "F:/20_Phototraps_photos/4 - FZS" \
        --manifest thumbs.gz --out restored/
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import sys
from collections import Counter, defaultdict

from PIL import Image
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.camera_traps.database import get_ct_engine  # noqa: E402
from app.camera_traps.utils import extract_datetime_from_exif  # noqa: E402

JPEG_EXTS = {'.jpg', '.jpeg'}


def load_manifest(path):
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


def scan_dir(thumb_dir):
    sizes = {}
    with os.scandir(thumb_dir) as it:
        for entry in it:
            if entry.is_file():
                sizes[entry.name] = entry.stat().st_size
    return sizes


# Directory names that are storage buckets rather than a camera: the DCIM
# convention (100CUDDY, 200CUDDY) and the dated dumps some rangers make
# (01_01_25). A bare number like 1904 is a camera code and must survive.
DCIM_BUCKET = re.compile(r'^(dcim|\d{3}[A-Za-z]+)$', re.IGNORECASE)
DATED_DUMP = re.compile(r'^\d{1,4}([_\-.]\d{1,4}){1,2}$')


def camera_folder(path, source_root):
    """The folder that stands for one camera deployment.

    Parks lay their exports out as
    <park>/<project>/01_Original_Upload/<...>/<camera>/<buckets>/file.JPG
    with the depth of <...> varying: Cheremoskyi names the camera folder
    directly ("DCIM Сосни 1601"), Synevyr nests it under a forestry district
    ("Остріцьке ПНДВ/1904"). So walk up from the file, dropping bucket-looking
    components, and stop at the first real name — never above the component
    that follows 01_Original_Upload.
    """
    rel = os.path.relpath(path, source_root).replace('\\', '/')
    parts = os.path.dirname(rel).split('/')
    floor = 1
    for i, part in enumerate(parts):
        if part.lower().startswith('01_original_upload'):
            floor = i + 2  # keep at least one component below the marker
            break
    while len(parts) > floor and (DCIM_BUCKET.match(parts[-1])
                                  or DATED_DUMP.match(parts[-1])):
        parts.pop()
    return '/'.join(parts)


def index_source(source_root, wanted_names):
    """Walk the originals and key them by (basename, EXIF datetime).

    `wanted_names` prunes the EXIF reads to the filenames the DB is actually
    missing — the tree holds far more frames than we need, and EXIF parsing
    dominates the runtime.
    """
    index = defaultdict(list)
    stats = Counter()
    for root, _dirs, files in os.walk(source_root):
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in JPEG_EXTS:
                continue
            stats['jpeg_seen'] += 1
            if fn not in wanted_names:
                stats['name_not_wanted'] += 1
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, 'rb') as fh:
                    dt = extract_datetime_from_exif(fh)
            except OSError as exc:
                stats['unreadable'] += 1
                print('  cannot read %s: %s' % (path, exc))
                continue
            if dt is None:
                stats['no_exif_date'] += 1
                continue
            stats['indexed'] += 1
            index[(fn, dt)].append(path)
    return index, stats


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', required=True,
                    help='root of the original photos supplied by the parks')
    ap.add_argument('--manifest',
                    help='server thumbnail listing (find -printf), for running '
                         'off-server over the tunnel')
    ap.add_argument('--out',
                    help='write the regenerated thumbnails here (omit = dry run)')
    ap.add_argument('--csv', help='write the per-row match decisions to this CSV')
    ap.add_argument('--partial', action='store_true',
                    help='also restore series where only some photos matched')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        config = app.config['CAMERA_TRAP_CONFIG']
        thumb_size = config['THUMBNAIL_SIZE']
        thumb_dir = os.path.join(config['UPLOAD_PATH'], 'pending_photos', 'thumbnails')

        if args.manifest:
            sizes = load_manifest(args.manifest)
        elif os.path.isdir(thumb_dir):
            sizes = scan_dir(thumb_dir)
        else:
            print('no thumbnail directory and no --manifest', file=sys.stderr)
            return 2

        engine = get_ct_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT p.id, p.observation_id, p.system_filename, p.original_filename,
                       p.captured_at, p.status, o.location_id, l.name AS location_name
                  FROM photos p
                  JOIN observations o ON o.id = p.observation_id
                  LEFT JOIN locations l ON l.id = o.location_id
                 WHERE p.status <> 'archived'
            """)).fetchall()

        broken = [r for r in rows if not sizes.get(r.system_filename)]
        print('broken photo rows (non-archived, no usable file): %d' % len(broken))
        if not broken:
            return 0

        with engine.connect() as conn:
            obs_totals = dict(conn.execute(text("""
                SELECT observation_id, count(*) FROM photos
                 WHERE observation_id = ANY(:ids) GROUP BY observation_id
            """), {'ids': list({r.observation_id for r in broken})}).fetchall())

        wanted = {r.original_filename for r in broken}
        print('distinct original filenames to look for: %d' % len(wanted))
        print('indexing originals under %s ...' % args.source)
        index, stats = index_source(args.source, wanted)
        print('  jpeg files seen        : %d' % stats['jpeg_seen'])
        print('  name not in the DB set : %d' % stats['name_not_wanted'])
        print('  indexed with EXIF date : %d' % stats['indexed'])
        print('  no usable EXIF date    : %d' % stats['no_exif_date'])
        if stats['unreadable']:
            print('  unreadable             : %d' % stats['unreadable'])

        # ── match ────────────────────────────────────────────────────────────
        matched, unmatched, ambiguous = {}, [], []
        for r in broken:
            captured = r.captured_at.replace(tzinfo=None)
            cands = index.get((r.original_filename, captured), [])
            if not cands:
                unmatched.append(r)
            elif len(cands) > 1:
                ambiguous.append((r, cands))
            else:
                matched[r.id] = (r, cands[0])
        print('\nmatched by filename + EXIF timestamp : %d' % len(matched))
        print('no original found yet                : %d' % len(unmatched))
        print('ambiguous (same name+time twice)     : %d' % len(ambiguous))

        # ── cross-check: folder <-> location must be one-to-one ──────────────
        folder_of_loc = defaultdict(set)
        loc_of_folder = defaultdict(set)
        for r, path in matched.values():
            folder = camera_folder(path, args.source)
            folder_of_loc[r.location_name].add(folder)
            loc_of_folder[folder].add(r.location_name)
        crossed = {loc for loc, f in folder_of_loc.items() if len(f) > 1}
        crossed |= {loc for folder, locs in loc_of_folder.items() if len(locs) > 1
                    for loc in locs}
        if crossed:
            print('\nREFUSED — folder/location mapping is not one-to-one:')
            for loc in sorted(crossed):
                print('   %s  <-  %s' % (loc, sorted(folder_of_loc[loc])))
        else:
            print('\nfolder -> location mapping (one-to-one for every match):')
            for loc in sorted(folder_of_loc):
                print('   %-45s <- %s' % (loc, next(iter(folder_of_loc[loc]))))

        # ── cross-check: one source file may not serve two rows ─────────────
        used = defaultdict(list)
        for pid, (r, path) in matched.items():
            used[path].append(pid)
        reused = {p: ids for p, ids in used.items() if len(ids) > 1}
        if reused:
            print('REFUSED — %d source files matched more than one row' % len(reused))

        # ── cross-check: whole series only ──────────────────────────────────
        broken_per_obs = Counter(r.observation_id for r in broken)
        matched_per_obs = Counter(r.observation_id for r, _ in matched.values())
        complete = {o for o in broken_per_obs
                    if matched_per_obs[o] == broken_per_obs[o]}
        touched = {o for o in matched_per_obs}
        print('series fully covered by the originals: %d of %d touched'
              % (len(complete), len(touched)))

        # ── final accept set ────────────────────────────────────────────────
        accepted, refused = {}, defaultdict(list)
        for pid, (r, path) in matched.items():
            if r.location_name in crossed:
                refused['folder/location not one-to-one'].append(pid)
            elif path in reused:
                refused['source file claimed by several rows'].append(pid)
            elif r.observation_id not in complete and not args.partial:
                refused['series only partially covered'].append(pid)
            else:
                accepted[pid] = (r, path)
        print('\nACCEPTED for restore: %d photos in %d series'
              % (len(accepted), len({r.observation_id for r, _ in accepted.values()})))
        for reason, ids in refused.items():
            print('  refused, %s: %d' % (reason, len(ids)))

        by_loc = Counter(r.location_name for r, _ in accepted.values())
        still = Counter(r.location_name for r in broken if r.id not in accepted)
        print('\nper location (restorable / still broken):')
        for loc in sorted(set(by_loc) | set(still)):
            print('  %-45s %5d / %5d' % (loc, by_loc.get(loc, 0), still.get(loc, 0)))

        if args.csv:
            with open(args.csv, 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(['photo_id', 'observation_id', 'location_name',
                            'original_filename', 'captured_at', 'system_filename',
                            'decision', 'source_path'])
                for r in broken:
                    if r.id in accepted:
                        decision, src = 'restore', accepted[r.id][1]
                    elif r.id in matched:
                        decision, src = 'refused', matched[r.id][1]
                    else:
                        decision, src = 'no source', ''
                    w.writerow([r.id, r.observation_id, r.location_name,
                                r.original_filename, r.captured_at,
                                r.system_filename, decision, src])
            print('\nwrote %s' % args.csv)

        if not args.out:
            print('\ndry run — nothing written. Pass --out DIR to render.')
            return 0

        os.makedirs(args.out, exist_ok=True)
        written = failed = 0
        for pid, (r, path) in sorted(accepted.items()):
            target = os.path.join(args.out, r.system_filename)
            try:
                with Image.open(path) as img:
                    exif = img.info.get('exif')
                    img.thumbnail(thumb_size)
                    if exif:
                        img.save(target, 'JPEG', quality=85, exif=exif)
                    else:
                        img.save(target, 'JPEG', quality=85)
                if os.path.getsize(target) == 0:
                    raise IOError('wrote 0 bytes')
                written += 1
            except Exception as exc:
                failed += 1
                print('  FAILED %s from %s: %s' % (r.system_filename, path, exc))
                if os.path.exists(target):
                    os.remove(target)
        print('\nrendered %d thumbnails into %s (%d failed)'
              % (written, args.out, failed))
        print('Install them with deploy/install_restored_thumbs.py on the server.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
