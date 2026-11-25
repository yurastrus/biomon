# SPDX-License-Identifier: AGPL-3.0-only
"""
Add the "flag for re-review" columns to pam_db.segments.

Run from the project root:
    venv/Scripts/python -m scripts.init_pam_review_flag      # Windows
    venv/bin/python -m scripts.init_pam_review_flag          # Linux

Idempotent (ADD COLUMN / CREATE INDEX IF NOT EXISTS). Run BEFORE deploying
code: the PAM admin page, the flagged list and the completed-verification
cleanup read segments.flagged. Mirrors app/pam/migrations/0011_segment_review_flag.sql.
"""
import os

from app import create_app
from app.pam.utils import get_pam_engine

_SQL = os.path.join(os.path.dirname(__file__), '..', 'app', 'pam',
                    'migrations', '0011_segment_review_flag.sql')


def main():
    app = create_app()
    with app.app_context():
        with open(os.path.abspath(_SQL), 'r', encoding='utf-8') as f:
            body = f.read()
        engine = get_pam_engine()
        print(f"pam_db: {engine.url!r}")
        with engine.begin() as conn:
            conn.exec_driver_sql(body)
            cols = conn.exec_driver_sql("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name = 'segments'
                  AND column_name IN ('flagged', 'flag_note', 'flagged_by', 'flagged_at')
                ORDER BY column_name""").fetchall()
            flagged = conn.exec_driver_sql("SELECT COUNT(*) FROM segments WHERE flagged").scalar()
        for c in cols:
            print(f"  segments.{c.column_name}: {c.data_type}")
        print(f"Flagged segments now: {flagged}")
        print("Done.")


if __name__ == '__main__':
    main()
