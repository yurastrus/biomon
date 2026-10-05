# SPDX-License-Identifier: AGPL-3.0-only
"""
Add the batch clock-correction column to upload_batches (/upload-fast time shift).

Run from the project root:
    venv/Scripts/python -m scripts.init_time_offset      # Windows
    venv/bin/python -m scripts.init_time_offset          # Linux

What it does (idempotent, ADD COLUMN IF NOT EXISTS):
    upload_batches.time_offset_seconds INTEGER NOT NULL DEFAULT 0

Existing batches get 0, i.e. "no shift", which is what they were uploaded with.

IMPORTANT: run BEFORE deploying the code. The model declares the column, so
every ORM query on UploadBatch (and process_single_photo's RETURNING) fails
until it exists. The reverse order is safe: older code, including
/var/www/myproject on an older shared-ct, ignores the extra column.

Why not Alembic: ct_db is not managed by Alembic, only
CTBase.metadata.create_all() + one-off DDL scripts (see also init_review_flag,
init_fast_upload).
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE upload_batches ADD COLUMN IF NOT EXISTS "
    "time_offset_seconds INTEGER NOT NULL DEFAULT 0",
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
