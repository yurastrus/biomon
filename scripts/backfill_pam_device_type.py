# SPDX-License-Identifier: AGPL-3.0-only
"""
Backfill service_visits.device_type_id for past installation visits.

Owner's statement (2026-11-02): every recorder installed in 2025-2026 was a
Song Meter Micro 2. Only installation visits with no model yet are touched;
nothing already set is overwritten.

Run from the project root (dry run by default):
    venv/Scripts/python -m scripts.backfill_pam_device_type            # show counts
    venv/Scripts/python -m scripts.backfill_pam_device_type --apply    # write

Undo: UPDATE service_visits SET device_type_id = NULL WHERE id IN (<ids printed>).
"""
import argparse

from sqlalchemy import text

from app import create_app
from app.pam import service_forecast
from app.pam.utils import get_pam_engine

DEVICE_NAME = 'SM Micro 2'
SINCE = '2025-01-01'

SELECT_SQL = text("""
    SELECT sv.id, sv.location_id, sv.visit_datetime
    FROM service_visits sv
    WHERE sv.visit_purpose_id = :install
      AND sv.device_type_id IS NULL
      AND sv.visit_datetime >= :since
    ORDER BY sv.visit_datetime
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write (default: dry run)')
    args = ap.parse_args()
    app = create_app()
    with app.app_context():
        engine = get_pam_engine()
        with engine.begin() as conn:
            device_id = conn.execute(text("SELECT id FROM device_types WHERE name_ua = :n"),
                                     {'n': DEVICE_NAME}).scalar_one()
            rows = conn.execute(SELECT_SQL, {'install': service_forecast.PURPOSE_INSTALL,
                                             'since': SINCE}).fetchall()
            older = conn.execute(text("""
                SELECT COUNT(*) FROM service_visits
                WHERE visit_purpose_id = :install AND device_type_id IS NULL AND visit_datetime < :since
            """), {'install': service_forecast.PURPOSE_INSTALL, 'since': SINCE}).scalar()
            print(f"{DEVICE_NAME} = id {device_id}")
            print(f"installation visits since {SINCE} without a model: {len(rows)} "
                  f"({len({r.location_id for r in rows})} locations)")
            print(f"older installation visits left as unknown: {older}")
            print("visit ids:", ','.join(str(r.id) for r in rows))
            if not args.apply:
                print("Dry run. Add --apply to write.")
                return
            n = conn.execute(text("""
                UPDATE service_visits SET device_type_id = :dev
                WHERE id = ANY(:ids) AND device_type_id IS NULL
            """), {'dev': device_id, 'ids': [r.id for r in rows]}).rowcount
            print(f"Updated {n} visits.")


if __name__ == '__main__':
    main()
