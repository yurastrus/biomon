# SPDX-License-Identifier: AGPL-3.0-only
"""
Backfill service_visits.device_type_id for past installation visits.

Owner's statement (2026-11-02): every recorder installed in 2025-2026 was a
Song Meter Micro 2. Only installation visits with no model yet are touched;
nothing already set is overwritten.

Run from the project root (dry run by default):
    venv/Scripts/python -m scripts.backfill_pam_device_type            # show counts
    venv/Scripts/python -m scripts.backfill_pam_device_type --apply    # write

Locations whose log has no installation (it starts with routine visits):
    venv/Scripts/python -m scripts.backfill_pam_device_type --locations 110,12 [--apply]
sets the model on the first visit of the current deployment (after the last
removal), unless a visit of that deployment already has one. Editing that
routine visit later keeps it (the update route uses COALESCE).

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
    ap.add_argument('--locations', help='comma-separated location ids without an installation record')
    args = ap.parse_args()
    if args.locations:
        return backfill_first_visits([int(x) for x in args.locations.split(',')], args.apply)
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


FIRST_VISIT_SQL = text("""
    WITH cur AS (
        SELECT sv.*
        FROM service_visits sv
        WHERE sv.location_id = :loc
          AND sv.visit_datetime > COALESCE((
                SELECT MAX(r.visit_datetime) FROM service_visits r
                WHERE r.location_id = :loc AND r.visit_purpose_id = :removed), '-infinity')
    )
    SELECT (SELECT id FROM cur ORDER BY visit_datetime LIMIT 1) AS first_id,
           (SELECT visit_datetime FROM cur ORDER BY visit_datetime LIMIT 1) AS first_dt,
           (SELECT COUNT(*) FROM cur WHERE device_type_id IS NOT NULL) AS with_model
""")


def backfill_first_visits(location_ids, apply):
    app = create_app()
    with app.app_context():
        with get_pam_engine().begin() as conn:
            device_id = conn.execute(text("SELECT id FROM device_types WHERE name_ua = :n"),
                                     {'n': DEVICE_NAME}).scalar_one()
            targets = []
            for loc in location_ids:
                r = conn.execute(FIRST_VISIT_SQL, {'loc': loc,
                                                   'removed': service_forecast.PURPOSE_REMOVED}).one()
                name = conn.execute(text("SELECT location_name FROM locations WHERE location_id = :l"),
                                    {'l': loc}).scalar()
                if r.first_id is None or r.with_model:
                    print(f"  {loc} {name}: skipped (no visits or model already set)")
                    continue
                print(f"  {loc} {name}: visit {r.first_id} of {r.first_dt:%Y-%m-%d} -> {DEVICE_NAME}")
                targets.append(r.first_id)
            print("visit ids:", ','.join(map(str, targets)))
            if not apply:
                print("Dry run. Add --apply to write.")
                return
            n = conn.execute(text("""
                UPDATE service_visits SET device_type_id = :dev
                WHERE id = ANY(:ids) AND device_type_id IS NULL
            """), {'dev': device_id, 'ids': targets}).rowcount
            print(f"Updated {n} visits.")


if __name__ == '__main__':
    main()
