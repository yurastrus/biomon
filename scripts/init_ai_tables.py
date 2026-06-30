# SPDX-License-Identifier: AGPL-3.0-only
"""
Create AI-runner tables in ct_db.

Run from the project root:
    venv/Scripts/python -m scripts.init_ai_tables      # Windows
    venv/bin/python -m scripts.init_ai_tables          # Linux

Use --drop to recreate tables (WARNING: destroys all prediction data):
    python -m scripts.init_ai_tables --drop

What it does:
    1. Connects to ct_db via CT_DATABASE_URI from .env.
    2. Checks which of the 3 tables (ai_models, ai_predictions, ai_run_queue) exist.
    3. Creates missing ones (CREATE TABLE IF NOT EXISTS via SQLAlchemy).
    4. Prints a summary.

Idempotent: safe to run without --drop.
"""

import argparse
import sys

from sqlalchemy import inspect

from app import create_app
from app.camera_traps.database import get_ct_engine
from app.camera_traps.models import AIModel, AIPrediction, AIRunQueue, AIControl


AI_TABLES = [
    AIModel.__table__, AIPrediction.__table__, AIRunQueue.__table__,
    AIControl.__table__,
]


def main():
    parser = argparse.ArgumentParser(description='Init AI-runner tables in ct_db')
    parser.add_argument(
        '--drop',
        action='store_true',
        help='WARNING: drop existing AI tables before creating them (data loss)',
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        engine = get_ct_engine()

        inspector = inspect(engine)
        existing = set(inspector.get_table_names())

        print(f"Connected to: {engine.url}")
        print()

        if args.drop:
            confirm = input(
                f"This will drop the tables {[t.name for t in AI_TABLES]} and all data in them.\n"
                f"Type 'DROP' to confirm: "
            )
            if confirm != 'DROP':
                print("Cancelled.")
                sys.exit(1)
            # drop order matters: dependents first (predictions references models)
            for table in reversed(AI_TABLES):
                if table.name in existing:
                    print(f"  DROP TABLE {table.name}")
                    table.drop(engine)

        # refresh inspector after possible drop
        existing = set(inspect(engine).get_table_names())

        print("Table status:")
        to_create = []
        for table in AI_TABLES:
            if table.name in existing:
                print(f"  ✓ {table.name} (already exists)")
            else:
                print(f"  + {table.name} (will be created)")
                to_create.append(table)

        if to_create:
            print()
            print(f"Creating {len(to_create)} tables...")
            for table in to_create:
                table.create(engine)
                print(f"  ✓ {table.name}")

            existing_after = set(inspect(engine).get_table_names())
            for table in to_create:
                if table.name not in existing_after:
                    print(f"  ✗ ERROR: {table.name} did not appear in the DB")
                    sys.exit(2)
        else:
            print("\nNothing to create.")

        # Seed the ai_control singleton row (id=1). Idempotent — the Flask pause
        # helpers also upsert it on first use, but seeding here guarantees the
        # row exists from the start. Safe to run every time.
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO ai_control (id, updated_at) VALUES (1, NOW()) "
                "ON CONFLICT (id) DO NOTHING"
            ))
        print("  ✓ ai_control singleton row ensured (id=1)")

        print("\nDone.")


if __name__ == '__main__':
    main()
