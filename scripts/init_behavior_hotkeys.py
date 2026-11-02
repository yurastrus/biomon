# SPDX-License-Identifier: AGPL-3.0-only
"""
Give behaviour tags fixed digit keys on /identify (behavior_types.hotkey).

Run from the project root (DRY RUN by default, nothing is changed):
    venv/Scripts/python -m scripts.init_behavior_hotkeys            # Windows, dry run
    venv/bin/python -m scripts.init_behavior_hotkeys --apply        # Linux, apply

What it does, in ONE transaction:
    1. behavior_types.hotkey INTEGER NULL, UNIQUE, CHECK 1..10 (10 = key "0").
    2. Only if no tag has a key yet: gives keys 1..10 to the ten tags in the
       order /identify shows them today (series count among completed and
       archived series, ties alphabetical by name_ua), i.e. the order
       verifiers already know. Later runs never reshuffle existing keys.

Why fixed keys: the usage ranking moves tags with close counts back and forth,
and a verifier who learnt "3 = feeding" must not find another tag there next
week. New tags get no key; giving or moving one is a deliberate admin act
(UPDATE behavior_types SET hotkey = ...), to be revisited if needed.

Order: run BEFORE deploying the code, the model declares the column.
ct_db is not Alembic-managed (create_all + one-off DDL, see init_time_offset).
"""
import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine

MAX_KEYS = 10

DDL = [
    "SET LOCAL lock_timeout = '5s'",
    "ALTER TABLE behavior_types ADD COLUMN IF NOT EXISTS hotkey INTEGER",
]
CONSTRAINTS = {
    'behavior_types_hotkey_key': "ALTER TABLE behavior_types ADD CONSTRAINT "
                                 "behavior_types_hotkey_key UNIQUE (hotkey)",
    'behavior_types_hotkey_range': "ALTER TABLE behavior_types ADD CONSTRAINT "
                                   "behavior_types_hotkey_range CHECK (hotkey BETWEEN 1 AND 10)",
}

# Same ranking as routes.get_behavior_ranking + the alphabetical tie-break of
# the identify() route.
RANKING_SQL = """
    SELECT bt.id, bt.name_ua, bt.name_en, COUNT(DISTINCT o.id) AS series
    FROM behavior_types bt
    LEFT JOIN identification_behaviors ib ON ib.behavior_type_id = bt.id
    LEFT JOIN identifications i ON i.id = ib.identification_id
    LEFT JOIN photos p ON p.id = i.photo_id
    LEFT JOIN observations o ON o.id = p.observation_id
                            AND o.status IN ('completed', 'archived')
    GROUP BY bt.id, bt.name_ua, bt.name_en
    ORDER BY series DESC, bt.name_ua
"""


def _has_column(conn):
    return conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'behavior_types' AND column_name = 'hotkey'")).scalar() is not None


def _constraint_names(conn):
    return {r[0] for r in conn.execute(text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'behavior_types'::regclass"))}


def main():
    apply = '--apply' in sys.argv
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        with engine.connect() as conn:
            where = conn.execute(text(
                "SELECT current_database(), inet_server_port()")).fetchone()
            print(f"Database: {where[0]} on server port {where[1]}")

            has_column = _has_column(conn)
            existing = _constraint_names(conn)
            assigned = 0
            if has_column:
                assigned = conn.execute(text(
                    "SELECT COUNT(*) FROM behavior_types WHERE hotkey IS NOT NULL")).scalar()

            ranking = conn.execute(text(RANKING_SQL)).fetchall()
            print(f"\nBehaviour tags: {len(ranking)}, with a key already: {assigned}")

            plan = list(DDL) + [sql for name, sql in CONSTRAINTS.items() if name not in existing]
            seed = []
            if assigned:
                print("Keys already given; they are kept as they are.")
                if has_column:
                    for r in conn.execute(text(
                            "SELECT hotkey, name_ua, name_en FROM behavior_types "
                            "WHERE hotkey IS NOT NULL ORDER BY hotkey")):
                        print(f"   key {r.hotkey % 10}: {r.name_ua} / {r.name_en}")
            else:
                seed = ranking[:MAX_KEYS]
                print("\nKeys to give (current /identify order):")
                for n, r in enumerate(seed, start=1):
                    print(f"   key {n % 10}: {r.name_ua} / {r.name_en}  ({r.series} series)")
                for r in ranking[MAX_KEYS:]:
                    print(f"   no key: {r.name_ua} / {r.name_en}  ({r.series} series)")

            print("\nPlanned SQL (one transaction):")
            for s in plan:
                print("   ", s + ';')
            for n, r in enumerate(seed, start=1):
                print(f"    UPDATE behavior_types SET hotkey = {n} WHERE id = {r.id};")
            if not apply:
                print("\nDRY RUN: nothing changed. Add --apply to execute.")
                conn.rollback()
                return

        with engine.begin() as conn:
            for s in plan:
                conn.execute(text(s))
            for n, r in enumerate(seed, start=1):
                conn.execute(text("UPDATE behavior_types SET hotkey = :k WHERE id = :id"),
                             {'k': n, 'id': r.id})
        print("\nDone.")


if __name__ == '__main__':
    main()
