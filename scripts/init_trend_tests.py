# SPDX-License-Identifier: AGPL-3.0-only
"""
Create the species_trend_tests table (ct_db) — Mann–Kendall trend-test results.

Run from the project root:
    venv/bin/python -m scripts.init_trend_tests        # Linux / prod
    venv/Scripts/python -m scripts.init_trend_tests    # Windows / dev

Idempotent:
    CREATE TABLE IF NOT EXISTS — safe to run multiple times.

Why not via create_all:
    ct_db is not managed by Alembic; CTBase.metadata.create_all() DOES create
    this table on a fresh dev DB (where this script is then a no-op), but on an
    existing production DB create_all() is not run as part of deploy, so the
    table must be created explicitly once. Mirrors scripts/init_analytics_status.py
    and scripts/init_fast_upload.py.

After running once, trigger an analytics recalculation ("Run analytics" in the
admin panel) so the table gets populated.
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS species_trend_tests (
        species_id    INTEGER       NOT NULL,
        scope_type    VARCHAR(20)   NOT NULL,
        scope_id      VARCHAR(100)  NOT NULL,
        n_years       INTEGER       NOT NULL,
        mk_tau        NUMERIC(10,4),
        mk_p          NUMERIC(10,6),
        trend         VARCHAR(20)   NOT NULL,
        sen_slope     NUMERIC(12,6),
        calculated_at TIMESTAMP,
        PRIMARY KEY (species_id, scope_type, scope_id)
    )
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
                print(f"  > {stmt[:90]}{'...' if len(stmt) > 90 else ''}")
                conn.execute(text(ddl))
        print()
        print("Done.")


if __name__ == '__main__':
    main()
