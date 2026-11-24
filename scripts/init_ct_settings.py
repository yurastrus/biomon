# SPDX-License-Identifier: AGPL-3.0-only
"""
Create ct_settings (admin-editable CT settings shared by all hosts,
app/camera_traps/ct_settings.py) and optionally seed the closing threshold.

Run from the project root (DRY RUN by default, nothing is changed):
    venv/Scripts/python -m scripts.init_ct_settings --threshold 1.49 --ai-factor 0.5          # dry run
    venv/bin/python -m scripts.init_ct_settings --threshold 1.49 --ai-factor 0.5 --apply      # apply

--threshold / --ai-factor seed a key only when it is absent; an existing
value (set on /admin/verifier-weights) is never overwritten.
Order: either way round; the code reads the config fallback while the
table or the key is missing.
"""
import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.ct_settings import AI_FACTOR_KEY, THRESHOLD_KEY, TABLE, parse
from app.camera_traps.database import get_ct_engine

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    key        VARCHAR(64) PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMP   NOT NULL DEFAULT now(),
    updated_by VARCHAR(150)
)
"""


def main():
    apply = '--apply' in sys.argv
    seeds = {}
    for flag, key in (('--threshold', THRESHOLD_KEY), ('--ai-factor', AI_FACTOR_KEY)):
        if flag in sys.argv:
            val = parse(key, sys.argv[sys.argv.index(flag) + 1])
            if val is None:
                sys.exit(f'{flag}: value out of range')
            seeds[key] = val
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        with engine.begin() as conn:
            exists = conn.execute(text("SELECT to_regclass(:t)"), {'t': TABLE}).scalar()
            current = {}
            if exists:
                current = dict(conn.execute(text(f'SELECT key, value FROM {TABLE}')).fetchall())
            print(f'table {TABLE}: {"exists" if exists else "missing"}; stored: {current}')
            if not apply:
                print(f'would seed (only absent keys): {seeds}')
                print('DRY RUN: nothing changed. Re-run with --apply.')
                return
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text(DDL))
            for key, val in seeds.items():
                if key in current:
                    print(f'{key} already {current[key]}; left as it is')
                    continue
                conn.execute(text(
                    f"INSERT INTO {TABLE} (key, value, updated_at, updated_by) "
                    f"VALUES (:k, :v, now(), 'init_ct_settings')"), {'k': key, 'v': str(val)})
                print(f'{key} set to {val}')
        print('done')


if __name__ == '__main__':
    main()
