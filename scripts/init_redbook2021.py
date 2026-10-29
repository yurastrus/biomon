# SPDX-License-Identifier: AGPL-3.0-only
"""
Add species.redbook2021 (Red Data Book of Ukraine 2021 category) to ct_db
and pam_db.

Run from the project root:
    venv/Scripts/python -m scripts.init_redbook2021      # Windows
    venv/bin/python -m scripts.init_redbook2021          # Linux

Idempotent. Run BEFORE deploying code: both models read the column.
ct_db is not Alembic-managed (one-off DDL scripts); for pam_db this mirrors
app/pam/migrations/0008_species_redbook2021.sql, the canonical psql form.
Then fill values: venv/bin/python -m scripts.redbook2021.import_redbook_2021 --apply
"""
import os
import re

from app import create_app
from app.camera_traps.database import get_ct_engine
from app.pam.utils import get_pam_engine

_PAM_SQL = os.path.join(os.path.dirname(__file__), '..', 'app', 'pam',
                        'migrations', '0008_species_redbook2021.sql')


def main():
    app = create_app()
    with app.app_context():
        with open(os.path.abspath(_PAM_SQL), 'r', encoding='utf-8') as f:
            body = re.sub(r'(?im)^\s*(BEGIN|COMMIT)\s*;\s*$', '', f.read())
        # Same DDL for both: the PAM migration file is the single source.
        for label, engine in (('ct_db', get_ct_engine()), ('pam_db', get_pam_engine())):
            print(f"{label}: {engine.url}")
            with engine.begin() as conn:
                conn.exec_driver_sql(body)
        print("Done. species.redbook2021 exists in ct_db and pam_db.")


if __name__ == '__main__':
    main()
