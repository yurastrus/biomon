# SPDX-License-Identifier: AGPL-3.0-only
"""
Create pam_db.device_types (recorder models) and service_visits.device_type_id.

Run from the project root:
    venv/Scripts/python -m scripts.init_pam_device_types      # Windows
    venv/bin/python -m scripts.init_pam_device_types          # Linux

Idempotent. Run BEFORE deploying code: manage-locations and the service plan
read the table. Mirrors app/pam/migrations/0009_device_types.sql, the
canonical psql form.
"""
import os
import re

from app import create_app
from app.pam.utils import get_pam_engine

_SQL = os.path.join(os.path.dirname(__file__), '..', 'app', 'pam',
                    'migrations', '0009_device_types.sql')


def main():
    app = create_app()
    with app.app_context():
        with open(os.path.abspath(_SQL), 'r', encoding='utf-8') as f:
            body = re.sub(r'(?im)^\s*(BEGIN|COMMIT)\s*;\s*$', '', f.read())
        engine = get_pam_engine()
        print(f"pam_db: {engine.url}")
        with engine.begin() as conn:
            conn.exec_driver_sql(body)
            rows = conn.exec_driver_sql(
                "SELECT id, name_ua, name_en FROM device_types ORDER BY sort_order, id").fetchall()
        for r in rows:
            print(f"  {r.id}: {r.name_ua} / {r.name_en}")
        print("Done. device_types and service_visits.device_type_id exist.")


if __name__ == '__main__':
    main()
