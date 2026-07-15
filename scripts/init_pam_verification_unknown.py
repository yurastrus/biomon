# SPDX-License-Identifier: AGPL-3.0-only
"""
Apply migration 0003 to pam_db: allow 'discarded' segment status and make the
update_segment_stats() trigger ignore "unknown" (verification_result=2) votes in
the consensus math.

Run from the project root:
    venv/Scripts/python -m scripts.init_pam_verification_unknown      # Windows
    venv/bin/python -m scripts.init_pam_verification_unknown          # Linux

Idempotent (widens a CHECK + CREATE OR REPLACE trigger). Mirrors
app/pam/migrations/0003_verification_unknown.sql — that .sql is the canonical
psql form; pam_db is not Alembic-managed.
"""
import os
import re

from app import create_app
from app.pam.utils import get_pam_engine

_SQL_PATH = os.path.join(os.path.dirname(__file__), '..', 'app', 'pam',
                         'migrations', '0003_verification_unknown.sql')


def main():
    app = create_app()
    with app.app_context():
        engine = get_pam_engine()
        print(f"Connected to: {engine.url}")
        with open(os.path.abspath(_SQL_PATH), 'r', encoding='utf-8') as f:
            sql = f.read()
        # Strip the file's own BEGIN;/COMMIT; — SQLAlchemy manages the txn below.
        body = re.sub(r'(?im)^\s*(BEGIN|COMMIT)\s*;\s*$', '', sql)
        # exec_driver_sql sends the raw string to psycopg, which runs the whole
        # multi-statement script (incl. the $function$ body) in one go, without
        # SQLAlchemy trying to parse ':=' etc. as bind params.
        with engine.begin() as conn:
            conn.exec_driver_sql(body)
        print("Done. 'discarded' status allowed; trigger ignores unknown votes.")


if __name__ == '__main__':
    main()
