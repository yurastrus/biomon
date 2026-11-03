"""Read-only look at recent CT uploads for locations matching a name pattern.

Written 2026-11-03 to check frames that a user cut from video with her own
script and uploaded to steppe locations (Tarutyne / Yelanets). Prints, per
batch: status and counts; per photo set: capture-time range, the 1900 no-EXIF
placeholder, duplicates of (file name, capture time), file-name patterns, and
how the photos were grouped into series. Touches nothing.

    venv/Scripts/python -m scripts.probe_recent_uploads "тарутин|єланец" 14
"""
import os
import re
import sys
from collections import Counter

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


def main(pattern, days):
    engine = create_engine(os.environ['CT_DATABASE_URL'])
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        locs = c.execute(text(
            "SELECT id, name FROM locations WHERE name ~* :p ORDER BY id"), {'p': pattern}).fetchall()
        print(f'{len(locs)} locations match /{pattern}/')
        if not locs:
            return
        names = dict(locs)

        batches = c.execute(text("""
            SELECT b.id, b.location_id, b.uploaded_by_id, b.status, b.total_files,
                   b.processed_files, b.created_at, b.completed_at, b.time_offset_seconds,
                   b.error_message
            FROM upload_batches b
            WHERE b.location_id = ANY(:ids) AND b.created_at > now() - make_interval(days => :d)
            ORDER BY b.created_at"""), {'ids': list(names), 'd': days}).fetchall()
        print(f'{len(batches)} batches in the last {days} days\n')

        for b in batches:
            print(f'=== batch {b.id} loc={b.location_id} {names[b.location_id]!r} '
                  f'user={b.uploaded_by_id} status={b.status} files={b.processed_files}/{b.total_files} '
                  f'created={b.created_at:%Y-%m-%d %H:%M} offset={b.time_offset_seconds} '
                  f'err={b.error_message!r}')
            photos = c.execute(text("""
                SELECT id, original_filename, captured_at, status, observation_id, sequence_number
                FROM photos WHERE upload_batch_id = :b ORDER BY captured_at, original_filename"""),
                {'b': b.id}).fetchall()
            if not photos:
                print('   no photos')
                continue
            times = [p.captured_at for p in photos]
            print(f'   photos={len(photos)} captured {min(times)} .. {max(times)}')
            print(f'   statuses={dict(Counter(p.status for p in photos))}')
            placeholder = sum(1 for t in times if t.year == 1900)
            if placeholder:
                print(f'   !! {placeholder} photos carry the 1900 no-EXIF placeholder')
            dup = Counter((p.original_filename, p.captured_at) for p in photos)
            dups = {k: v for k, v in dup.items() if v > 1}
            if dups:
                print(f'   !! {len(dups)} duplicated (filename, captured_at) keys')
            same_time = Counter(times)
            crowded = sum(1 for v in same_time.values() if v > 1)
            print(f'   capture times shared by >1 photo: {crowded} '
                  f'(max {max(same_time.values())} photos in one second)')
            stems = Counter(re.sub(r'\d+', '#', p.original_filename) for p in photos)
            print(f'   filename patterns: {stems.most_common(5)}')
            print(f'   first: {[ (p.original_filename, str(p.captured_at)) for p in photos[:4]]}')
            obs = Counter(p.observation_id for p in photos)
            sizes = Counter(obs.values())
            print(f'   series={len([o for o in obs if o])} ungrouped={obs.get(None, 0)} '
                  f'series sizes={sorted(sizes.items())[:12]}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'тарутин|єланец',
         int(sys.argv[2]) if len(sys.argv) > 2 else 14)
