"""
Make PAM locations private except the public ones of a few institutions.

Why (2026-11-27): PAM filters anonymous visitors to public locations
(``locations.visibility_level = 0``) exactly like camera traps, but the PAM
location form inserted every new location with ``visibility_level = 0``. So
nearly every recorder of every institution was public and anonymous visitors
saw all PAM data. The form now inserts 1 (private); this script fixes the
existing rows.

Rule: a location stays public only if it is public now AND belongs to one of
``PUBLIC_INSTITUTION_CODES`` (it may also belong to others, e.g. YNNP+RSNR).
The list was chosen by the owner on 2026-11-27: Roztochya reserve (RSNR),
Potelych (POTELYCH), azazellj, Obroshyne (OBROSHYNE).
Every other public location becomes private (1). Nothing is made public.
Logged-in users keep seeing the locations of their own institutions; admins
see everything.

Dry run by default (lists what would change); ``--apply`` writes. Idempotent.
The list of changed ids is printed so the change can be reverted with
``UPDATE locations SET visibility_level = 0 WHERE location_id IN (...)``.

Run (tunnel 5433 = prod PAM DB, or on the server):
    venv/Scripts/python -m scripts.set_pam_location_visibility [--apply]
    venv/bin/python -m scripts.set_pam_location_visibility [--apply]
"""

import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

PUBLIC_INSTITUTION_CODES = ['RSNR', 'POTELYCH', 'azazellj', 'OBROSHYNE']

TO_PRIVATE_SQL = """
    SELECT l.location_id, l.location_name,
           string_agg(DISTINCT i.code, ',' ORDER BY i.code) AS codes
    FROM locations l
    LEFT JOIN location_institutions li ON li.location_id = l.location_id
    LEFT JOIN institutions i ON i.id = li.institution_id
    WHERE l.visibility_level = 0
      AND NOT EXISTS (
          SELECT 1 FROM location_institutions lk
          JOIN institutions ik ON ik.id = lk.institution_id
          WHERE lk.location_id = l.location_id AND ik.code = ANY(:codes))
    GROUP BY l.location_id, l.location_name
    ORDER BY codes, l.location_id
"""


def main(apply):
    engine = create_engine(os.environ['PAM_DATABASE_URL'])
    with engine.begin() as conn:
        rows = conn.execute(text(TO_PRIVATE_SQL), {'codes': PUBLIC_INSTITUTION_CODES}).fetchall()
        for r in rows:
            print(f'{r.location_id:>5}  {r.codes or "-":<20} {r.location_name}')
        ids = [r.location_id for r in rows]
        print(f'\n{len(ids)} public locations outside {",".join(PUBLIC_INSTITUTION_CODES)} -> private')
        print('ids:', ','.join(map(str, ids)) or '-')
        if apply and ids:
            n = conn.execute(text(
                'UPDATE locations SET visibility_level = 1 '
                'WHERE location_id = ANY(:ids) AND visibility_level = 0'), {'ids': ids}).rowcount
            print(f'updated {n}')
        elif not apply:
            print('dry run, nothing written (use --apply)')
        left = conn.execute(text(
            'SELECT visibility_level, count(*) FROM locations GROUP BY 1 ORDER BY 1')).fetchall()
        print('visibility_level counts now:', [tuple(r) for r in left])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    parser.add_argument('--apply', action='store_true')
    main(parser.parse_args().apply)
