# SPDX-License-Identifier: AGPL-3.0-only
"""
Apply migration 0002 to the PAM database (pam_db): add the segments→recordings/
detections link columns + indexes.

Run from the project root:
    venv/Scripts/python -m scripts.init_pam_segment_links      # Windows
    venv/bin/python -m scripts.init_pam_segment_links          # Linux

What it does (idempotent, additive only — safe to re-run):
    1. ALTER TABLE segments ADD COLUMN recording_id / detection_id (nullable).
    2. Add FKs NOT VALID (metadata-only lock, no table scan).
    3. Partial indexes on the two columns.

This mirrors app/pam/migrations/0002_segments_detection_links.sql — that .sql is
the canonical psql form; this script is the create_all()-style convenience for
dev/prod since pam_db is not Alembic-managed.

After this, run the backfill:
    venv/Scripts/python -m scripts.backfill_pam_segment_links --report
    venv/Scripts/python -m scripts.backfill_pam_segment_links --apply
"""
from sqlalchemy import text

from app import create_app
from app.pam.utils import get_pam_engine


DDL_STATEMENTS = [
    "ALTER TABLE segments ADD COLUMN IF NOT EXISTS recording_id BIGINT",
    "ALTER TABLE segments ADD COLUMN IF NOT EXISTS detection_id BIGINT",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_segments_recording') THEN
            ALTER TABLE segments ADD CONSTRAINT fk_segments_recording
                FOREIGN KEY (recording_id) REFERENCES recordings(recording_id)
                ON DELETE SET NULL NOT VALID;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_segments_detection') THEN
            ALTER TABLE segments ADD CONSTRAINT fk_segments_detection
                FOREIGN KEY (detection_id) REFERENCES detections(detection_id)
                ON DELETE SET NULL NOT VALID;
        END IF;
    END $$;
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_segments_detection_id
        ON segments (detection_id) WHERE detection_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_segments_recording_id
        ON segments (recording_id) WHERE recording_id IS NOT NULL
    """,
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
                print(f"  > {stmt[:110]}{'...' if len(stmt) > 110 else ''}")
                conn.execute(text(ddl))
        print()
        print("Done. segments now has recording_id / detection_id.")


if __name__ == '__main__':
    main()
