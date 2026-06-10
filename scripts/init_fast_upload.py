"""
Prepare ct_db for the fast-upload route (/upload-fast).

Run from the project root:
    venv/Scripts/python -m scripts.init_fast_upload      # Windows
    venv/bin/python -m scripts.init_fast_upload          # Linux

What it does:
    1. Connects to ct_db via CT_DATABASE_URI.
    2. CREATE INDEX IF NOT EXISTS idx_photos_batch_captured
       ON photos(upload_batch_id, captured_at, id)
       — required by the CTE grouping query (LAG OVER ORDER BY captured_at).

Idempotent: safe to run multiple times.

Why not via Alembic:
    ct_db is not managed by Alembic — only CTBase.metadata.create_all().
    create_all() does NOT add indexes to existing tables, so a one-off
    DDL script is needed. If ct_db is ever migrated to Alembic, this index
    should be included in the corresponding revision.
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    """
    CREATE INDEX IF NOT EXISTS idx_photos_batch_captured
        ON photos (upload_batch_id, captured_at, id)
    """,
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
