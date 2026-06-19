# SPDX-License-Identifier: AGPL-3.0-only
"""
Add async analytics status columns to calculation_log (ct_db):
status / started_at / error_message.

Run from the project root:
    venv/bin/python -m scripts.init_analytics_status        # Linux / prod
    venv/Scripts/python -m scripts.init_analytics_status    # Windows / dev

Idempotent:
    ADD COLUMN IF NOT EXISTS — safe to run multiple times.

Why not via create_all:
    ct_db is not managed by Alembic; CTBase.metadata.create_all() does NOT
    add new columns to existing tables — it only creates missing tables.
    calculation_log already exists, so columns must be added via ALTER.
    On a fresh dev DB the table is created with all columns via create_all
    and this script becomes a no-op.
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE calculation_log ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'idle'",
    "ALTER TABLE calculation_log ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
    "ALTER TABLE calculation_log ADD COLUMN IF NOT EXISTS error_message TEXT",
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
                print(f"  > {stmt[:90]}{'...' if len(stmt) > 90 else ''}")
                conn.execute(text(ddl))
        print()
        print("Done.")


if __name__ == '__main__':
    main()
