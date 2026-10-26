"""Renumber photos.sequence_number in existing CT series in shooting order.

Until 2026-10-26 ties within one EXIF second were broken by photo id (upload
order), which the parallel /upload-fast pool makes random, so burst frames got
shuffled numbers. New grouping orders by SERIES_PHOTO_ORDER_SQL
(captured_at, original_filename COLLATE "C", id); this script brings existing
series in line with it. Viewers sort on the fly and don't need it; the numbers
are shown in the photo viewer and the gallery.

Idempotent: only rows whose number differs are updated.

Usage:
    venv/Scripts/python -m scripts.renumber_series_photos           # dry run
    venv/Scripts/python -m scripts.renumber_series_photos --apply   # write
    venv/bin/python -m scripts.renumber_series_photos --apply       # prod
"""
import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# Kept in sync with app.camera_traps.utils.SERIES_PHOTO_ORDER_SQL (a test
# checks it); not imported to avoid pulling the Flask app into a DB script.
ORDER_SQL = 'captured_at, original_filename COLLATE "C", id'

DIFF_CTE = f"""
    WITH want AS (
        SELECT id,
               observation_id,
               ROW_NUMBER() OVER (PARTITION BY observation_id
                                  ORDER BY {ORDER_SQL}) AS seq
          FROM photos
         WHERE observation_id IS NOT NULL
    )
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true', help='write the changes')
    args = ap.parse_args()

    url = os.environ.get('CT_DATABASE_URL') or os.environ['CT_DATABASE_URI']
    eng = create_engine(url)
    with eng.begin() as c:
        n_rows, n_obs = c.execute(text(DIFF_CTE + """
            SELECT COUNT(*), COUNT(DISTINCT w.observation_id)
              FROM want w JOIN photos p ON p.id = w.id
             WHERE p.sequence_number IS DISTINCT FROM w.seq
        """)).one()
        print(f"photos to renumber: {n_rows} in {n_obs} series")
        if not args.apply:
            print("dry run, nothing written (use --apply)")
            return
        res = c.execute(text(DIFF_CTE + """
            UPDATE photos p
               SET sequence_number = w.seq
              FROM want w
             WHERE p.id = w.id
               AND p.sequence_number IS DISTINCT FROM w.seq
        """))
        print(f"updated: {res.rowcount}")


if __name__ == '__main__':
    main()
