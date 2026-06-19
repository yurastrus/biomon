# SPDX-License-Identifier: AGPL-3.0-only
"""
Add a recording duration column to the recordings table (PAM coverage #37).

Run from the project root:
    venv/Scripts/python -m scripts.init_recording_duration      # Windows
    venv/bin/python -m scripts.init_recording_duration          # Linux

What it does (idempotent):
    recordings.duration_minutes NUMERIC(6,2) NOT NULL DEFAULT 5

    DEFAULT 5 immediately populates all existing rows (all recordings in
    the system are 5-minute recordings), so no separate backfill is needed.
    Duration is set on import going forward (pam/import form field).

Backwards-compatible (NOT NULL DEFAULT — existing code unaware of the column works).
Deploy together with the code changes for #37.

Why not Alembic: pam_db is not managed by Alembic — one-off DDL scripts only.
"""

from sqlalchemy import text

from app import create_app
from app.pam.utils import get_pam_engine


DDL_STATEMENTS = [
    "ALTER TABLE recordings ADD COLUMN IF NOT EXISTS "
    "duration_minutes NUMERIC(6,2) NOT NULL DEFAULT 5",
]


def main():
    app = create_app()
    with app.app_context():
        engine = get_pam_engine()
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
