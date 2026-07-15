# SPDX-License-Identifier: AGPL-3.0-only
"""
Prepare ct_db for the "auto-assign biotopes from landcover" admin tool.

Run from the project root:
    venv/Scripts/python -m scripts.init_biotope_autoassign      # Windows
    venv/bin/python -m scripts.init_biotope_autoassign          # Linux/prod

What it does:
    1. Connects to ct_db via CT_DATABASE_URI.
    2. Creates the biotope_landcover_map table if it does not exist
       (CTBase.metadata.create_all for that one table).
    3. Seeds conservative default mappings (ESA WorldCover class → biotope),
       matching biotopes by name_ua. Only classes not already mapped are
       inserted (ON CONFLICT DO NOTHING) and only when the named biotope exists,
       so the seed is safe on any installation and idempotent.

Idempotent: safe to run multiple times. On installations with different biotope
names the seed simply inserts nothing — the admin fills the mapping via the UI.

Why not via Alembic:
    ct_db is not managed by Alembic — only CTBase.metadata.create_all(). This
    one-off script owns both the DDL and the seed.
"""
import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine
from app.camera_traps.models import CTBase, BiotopeLandcoverMap
from app.camera_traps.biotope_autoassign import (
    DEFAULT_SEED_BY_NAME_UA, WORLDCOVER_CLASSES,
)


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        print(f"Connected to: {engine.url}")
        print()

        # 1. Create the table (no-op if it already exists).
        print("  > CREATE TABLE IF NOT EXISTS biotope_landcover_map")
        CTBase.metadata.create_all(engine, tables=[BiotopeLandcoverMap.__table__])

        # 2. Seed conservative defaults by biotope name_ua.
        seeded, skipped_missing_biotope, skipped_existing = 0, 0, 0
        with engine.begin() as conn:
            for wc_class, name_ua in DEFAULT_SEED_BY_NAME_UA.items():
                bid = conn.execute(
                    text("SELECT id FROM biotopes WHERE name_ua = :n"),
                    {"n": name_ua},
                ).scalar()
                label = WORLDCOVER_CLASSES.get(wc_class, ('?', '?'))[0]
                if bid is None:
                    print(f"    - class {wc_class} ({label}): biotope '{name_ua}' "
                          f"not found → skipped")
                    skipped_missing_biotope += 1
                    continue
                res = conn.execute(
                    text("""
                        INSERT INTO biotope_landcover_map (worldcover_class, biotope_id)
                        VALUES (:c, :b)
                        ON CONFLICT (worldcover_class) DO NOTHING
                    """),
                    {"c": wc_class, "b": bid},
                )
                if (res.rowcount or 0) == 1:
                    print(f"    + class {wc_class} ({label}) → '{name_ua}' (id={bid})")
                    seeded += 1
                else:
                    print(f"    = class {wc_class} ({label}): already mapped → left as is")
                    skipped_existing += 1

        print()
        print(f"Done. Seeded {seeded}, already-mapped {skipped_existing}, "
              f"missing-biotope {skipped_missing_biotope}.")


if __name__ == '__main__':
    main()
