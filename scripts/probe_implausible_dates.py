"""List CT series whose photos carry an implausible capture date (read-only).

Photos without EXIF get the 1900-01-01 placeholder on upload; a camera reset
to its factory clock gives 2000 or similar. Such series, once verified, used
to leak into the precomputed analytics (a "1900" year on the species-trends
page). This prints every series with a photo before EXIF_MIN_VALID_DATE or in
the future, with its batch, location and verified species, so the dates can
be fixed by hand (or the series left unverified).

Usage:
    venv/Scripts/python -m scripts.probe_implausible_dates
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app import create_app


SQL = text("""
    SELECT o.id AS obs_id, o.status, o.location_id, l.name AS location,
           p.upload_batch_id AS batch_id,
           MIN(p.captured_at) AS first_at, MAX(p.captured_at) AS last_at,
           COUNT(*) AS photos,
           MIN(p.original_filename) AS first_file,
           (SELECT string_agg(DISTINCT s.scientific_name, ', ')
              FROM identifications i
              JOIN photos p2 ON p2.id = i.photo_id
              JOIN species s ON s.id = i.species_id
             WHERE p2.observation_id = o.id) AS species
      FROM photos p
      JOIN observations o ON o.id = p.observation_id
      LEFT JOIN locations l ON l.id = o.location_id
     WHERE p.captured_at < :min_dt OR p.captured_at > :max_dt
     GROUP BY o.id, o.status, o.location_id, l.name, p.upload_batch_id
     ORDER BY o.status, o.id
""")


def main():
    app = create_app()
    with app.app_context():
        from app.camera_traps.database import get_ct_engine
        from app.camera_traps.utils import EXIF_MIN_VALID_DATE
        max_dt = datetime.now() + timedelta(days=2)
        with get_ct_engine().connect() as conn:
            rows = conn.execute(SQL, {'min_dt': EXIF_MIN_VALID_DATE,
                                      'max_dt': max_dt}).mappings().all()
        print(f'{len(rows)} series with photos outside '
              f'{EXIF_MIN_VALID_DATE.date()} .. {max_dt.date()}')
        for r in rows:
            print(f"obs {r['obs_id']} [{r['status']}] loc {r['location_id']} "
                  f"{r['location']!r} batch {r['batch_id']} "
                  f"{r['first_at']} .. {r['last_at']} ({r['photos']} ph, "
                  f"{r['first_file']}) species: {r['species']}")


if __name__ == '__main__':
    main()
