# SPDX-License-Identifier: AGPL-3.0-only
"""
Allow one verifier to give several species for one camera-trap series.

Run from the project root (DRY RUN by default, nothing is changed):
    venv/Scripts/python -m scripts.init_multi_species             # Windows, dry run
    venv/bin/python -m scripts.init_multi_species --apply         # Linux, apply
    venv/bin/python -m scripts.init_multi_species --revert --apply

What it does, in ONE transaction:
    identifications: UNIQUE (photo_id, user_id)
                  -> UNIQUE NULLS NOT DISTINCT (photo_id, user_id, species_id)

The new key still forbids the same species twice from one person on one photo
(a double-clicked submit), and NULLS NOT DISTINCT keeps that true for the 11
legacy rows whose species_id is NULL. Building the index takes a SHARE lock on
identifications (reads go on, writes wait ~1-2 s); lock_timeout makes the script
give up rather than queue behind a long transaction and stall the site.

Order: run BEFORE deploying the multi-species code. Old code keeps working with
the new key (it never writes a second row). --revert restores the old key and
refuses while any (photo, user) pair already has more than one row.

Why not Alembic: ct_db is managed by create_all() + one-off DDL scripts (see
init_time_offset, init_review_flag).
"""
import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine

OLD = '_photo_user_uc'
NEW = '_photo_user_species_uc'

FORWARD = [
    "SET LOCAL lock_timeout = '5s'",
    f"ALTER TABLE identifications ADD CONSTRAINT {NEW} "
    "UNIQUE NULLS NOT DISTINCT (photo_id, user_id, species_id)",
    f"ALTER TABLE identifications DROP CONSTRAINT {OLD}",
]
REVERT = [
    "SET LOCAL lock_timeout = '5s'",
    f"ALTER TABLE identifications ADD CONSTRAINT {OLD} UNIQUE (photo_id, user_id)",
    f"ALTER TABLE identifications DROP CONSTRAINT {NEW}",
]


def _constraints(conn):
    return dict(conn.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'identifications'::regclass AND contype = 'u'")).fetchall())


def main():
    apply = '--apply' in sys.argv
    revert = '--revert' in sys.argv
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        with engine.connect() as conn:
            where = conn.execute(text(
                "SELECT current_database(), inet_server_port()")).fetchone()
            print(f"Database: {where[0]} on server port {where[1]} "
                  f"({'SANDBOX' if where[1] == 5544 else 'PRODUCTION'})")
            before = _constraints(conn)
            print("Unique constraints now:", before or '(none)')

            if revert:
                dup = conn.execute(text(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM identifications "
                    "GROUP BY photo_id, user_id HAVING COUNT(*) > 1) t")).scalar()
                print(f"(photo, user) pairs with several rows: {dup}")
                if OLD in before and NEW not in before:
                    print("Already on the old key. Nothing to do.")
                    return
                if dup:
                    sys.exit("REFUSING: several species are already stored; the old key would not fit.")
                plan = REVERT
            else:
                if NEW in before and OLD not in before:
                    print("Already migrated. Nothing to do.")
                    return
                plan = FORWARD

            print("\nPlanned SQL (one transaction):")
            for s in plan:
                print("   ", s + ';')
            if not apply:
                print("\nDRY RUN: nothing changed. Add --apply to execute.")
                conn.rollback()
                return

        with engine.begin() as conn:
            for s in plan:
                conn.execute(text(s))
        with engine.connect() as conn:
            print("\nDone. Unique constraints now:", _constraints(conn))


if __name__ == '__main__':
    main()
