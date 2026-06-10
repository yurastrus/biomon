"""
Add "flag for re-review" columns to observations (Idea 6).

Run from the project root:
    venv/Scripts/python -m scripts.init_review_flag      # Windows
    venv/bin/python -m scripts.init_review_flag          # Linux

What it does (idempotent, ADD COLUMN IF NOT EXISTS):
    observations.flagged   BOOLEAN NOT NULL DEFAULT FALSE
    observations.flag_note TEXT

Backwards-compatible: new nullable/default columns — existing code unaware of
them (including /var/www/myproject with the older shared-ct) works unchanged.
IMPORTANT: deploy together with the code that uses the flag; applying the schema
change alone will not break anything, but missing columns crash the new logic.

Why not Alembic: ct_db is not managed by Alembic — only
CTBase.metadata.create_all() + one-off DDL scripts (see also init_fast_upload,
init_query_indexes).
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE observations ADD COLUMN IF NOT EXISTS "
    "flagged BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE observations ADD COLUMN IF NOT EXISTS flag_note TEXT",
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
