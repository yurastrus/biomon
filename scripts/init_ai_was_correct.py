"""
Add the was_correct column to ai_predictions (Idea 4).

Run from the project root:
    venv/Scripts/python -m scripts.init_ai_was_correct      # Windows
    venv/bin/python -m scripts.init_ai_was_correct          # Linux

What it does (idempotent):
    ai_predictions.was_correct BOOLEAN NULL
        — populated at consensus time (mark_observation_complete):
          True/False = prediction matched/did not match the consensus species,
          NULL = not yet evaluated, or AI did not identify a species.

Backwards-compatible (nullable). Deploy together with the corresponding code.
Historical completed series are backfilled separately: scripts.backfill_was_correct.

Why not Alembic: ct_db is not managed by Alembic — only
CTBase.metadata.create_all() + one-off DDL scripts.
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS was_correct BOOLEAN",
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
                print(f"  > {stmt}")
                conn.execute(text(ddl))
        print()
        print("Готово.")


if __name__ == '__main__':
    main()
