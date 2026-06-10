"""
Create indexes for common ct_db queries (dashboard, cleanup, contributor).

Run from the project root:
    venv/Scripts/python -m scripts.init_query_indexes      # Windows
    venv/bin/python -m scripts.init_query_indexes          # Linux

What it does:
    1. Connects to ct_db via CT_DATABASE_URI.
    2. CREATE INDEX IF NOT EXISTS idx_photos_status
       ON photos(status)            — cleanup (status='completed'/'pending'),
                                       dashboard photo-status filters.
    3. CREATE INDEX IF NOT EXISTS idx_identifications_user_id
       ON identifications(user_id)  — dashboard top-contributors (GROUP BY
                                       user_id), contributor page.

Idempotent: safe to run multiple times.

Production note (as of 2026-06):
    Both indexes already exist on prod and are actively used
    (verified with EXPLAIN ANALYZE: Index/Bitmap Index Scan).
    The script is a no-op on the live ct_db (IF NOT EXISTS).
    Only needed for fresh/dev installs with small tables.

    To build these indexes on an already-large table in production,
    do NOT use plain CREATE INDEX (ACCESS EXCLUSIVE lock blocks
    reads/writes during the build). Use outside a transaction:
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_photos_status
            ON photos(status);
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_identifications_user_id
            ON identifications(user_id);

Why separate from init_fast_upload / not via Alembic:
    ct_db is not managed by Alembic — only CTBase.metadata.create_all().
    create_all() does NOT add indexes to existing tables (same pattern
    as scripts/init_fast_upload.py).
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_photos_status ON photos (status)",
    "CREATE INDEX IF NOT EXISTS idx_identifications_user_id "
    "ON identifications (user_id)",
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
        print("Готово.")


if __name__ == '__main__':
    main()
