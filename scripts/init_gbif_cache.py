# SPDX-License-Identifier: AGPL-3.0-only
"""
Create the GBIF cache tables of the gap analysis page in ct_db.

Run from the project root:
    venv/Scripts/python -m scripts.init_gbif_cache      # Windows/dev (tunnel 5433)
    venv/bin/python -m scripts.init_gbif_cache          # Linux/prod

Creates gbif_taxa and gbif_cell_counts if they do not exist. Idempotent.
Filling them is a separate step: the "Оновити GBIF" button on the gap analysis
page (admin), or `--refresh` here to run it in the foreground.

ct_db is not managed by Alembic, so this script owns the DDL.
"""
import sys

from app import create_app
from app.camera_traps.database import get_ct_engine
from app.camera_traps.models import CTBase, GbifCellCount, GbifTaxon


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        print(f"Connected to: {engine.url}")
        print("  > CREATE TABLE IF NOT EXISTS gbif_taxa, gbif_cell_counts")
        CTBase.metadata.create_all(engine, tables=[GbifTaxon.__table__, GbifCellCount.__table__])
        if '--refresh' in sys.argv:
            from app.camera_traps.gbif_cache import refresh_gbif_cache
            summary = refresh_gbif_cache(engine, progress=lambda m: print('   ', m))
            print("  > refreshed:", summary)
        print("Done.")


if __name__ == '__main__':
    main()
