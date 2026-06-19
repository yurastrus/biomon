# SPDX-License-Identifier: AGPL-3.0-only
"""
Add the admin-only data-validity flag to camera-trap locations.

Run from the project root:
    venv/Scripts/python -m scripts.init_location_validity_ct      # Windows
    venv/bin/python -m scripts.init_location_validity_ct          # Linux

What it does (idempotent, ADD COLUMN IF NOT EXISTS):
    locations.is_valid     BOOLEAN NOT NULL DEFAULT TRUE
    locations.invalid_note TEXT

Existing locations stay valid (default TRUE). When an admin marks a location
invalid, its data is excluded from dashboards, exports, and analytics
aggregation.

Backwards-compatible: new columns with a default — code unaware of them keeps
working. Deploy together with the code that uses the flag.

Why not Alembic: ct_db is not managed by Alembic — only
CTBase.metadata.create_all() + one-off DDL scripts (see also init_review_flag,
init_fast_upload, init_query_indexes).
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE locations ADD COLUMN IF NOT EXISTS "
    "is_valid BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE locations ADD COLUMN IF NOT EXISTS invalid_note TEXT",
]


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        print(f"Connected to: {engine.url}")
        print()
        with engine.begin() as conn:
            for ddl in DDL_STATEMENTS:
                stmt = ' '.join(ddl.split())
                print(f"  > {stmt}")
                conn.execute(text(ddl))
        print()
        print("Done.")


if __name__ == '__main__':
    main()
