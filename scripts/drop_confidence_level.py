# SPDX-License-Identifier: AGPL-3.0-only
"""
Drops the dead identifications.confidence_level column from ct_db (#46).

The column was completely empty (the identification form never populated it —
an architectural leftover, found by diagnostics #41).

Run from the project root:
    venv/Scripts/python -m scripts.drop_confidence_level          # Windows
    venv/bin/python -m scripts.drop_confidence_level              # Linux

SAFETY: the script re-checks that the column is empty (COUNT(confidence_level)=0)
and ABORTS without changes if it finds any non-empty value. The DROP and the
check are in a single transaction.

WARNING: do NOT confuse this with pam_db.segments.confidence_level (a different
DB, actively used). This is ONLY ct_db.identifications.confidence_level.
Apply on prod TOGETHER with the code deploy (the model no longer has this
column) + coordinate /var/www/myproject.
"""

import sys
from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        print(f"Connected to: {engine.url}")
        with engine.begin() as conn:
            nn = conn.execute(text(
                "SELECT COUNT(confidence_level) FROM identifications")).scalar()
            total = conn.execute(text(
                "SELECT COUNT(*) FROM identifications")).scalar()
            print(f"confidence_level non-null: {nn} of {total}")
            if nn and nn > 0:
                print("ABORTED: the column is NOT empty — nothing was dropped.")
                sys.exit(1)
            print("  > ALTER TABLE identifications DROP COLUMN IF EXISTS confidence_level")
            conn.execute(text(
                "ALTER TABLE identifications DROP COLUMN IF EXISTS confidence_level"))
        print("Done: confidence_level dropped.")


if __name__ == '__main__':
    main()
