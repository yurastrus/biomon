# SPDX-License-Identifier: AGPL-3.0-only
"""
Create and fill verifier_species_weights (people's records for the weighted
closing rule, app/camera_traps/consensus.py, owner's decision 2026-11-23).

Run from the project root (DRY RUN by default, nothing is changed):
    venv/Scripts/python -m scripts.init_verifier_weights            # Windows, dry run
    venv/bin/python -m scripts.init_verifier_weights --apply        # Linux, apply

Dry run: computes the records from the current votes and prints a summary.
--apply: CREATE TABLE IF NOT EXISTS, then rebuilds it (same as the automatic
background refresh). Safe to re-run any time: it is also the manual refresh.

Order: either way round. The code falls back to the majority rule while the
table is missing or empty; the weighted rule starts with the first vote
after this has filled the table. Finished series are never recounted.
ct_db is not Alembic-managed (create_all + one-off DDL, see init_time_offset).
"""
import sys
from collections import Counter

from sqlalchemy import text

from app import create_app
from app.camera_traps.consensus import verifier_weight
from app.camera_traps.database import get_ct_engine
from app.camera_traps.verifier_weights import TABLE, compute, refresh

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    user_id    INTEGER   NOT NULL,
    species_id INTEGER   NOT NULL,
    n          INTEGER   NOT NULL,
    confirmed  INTEGER   NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, species_id)
)
"""


def summary(graded, k=1):
    users = {u for u, _s in graded}
    weights = [verifier_weight(c, n, k) for (n, c) in graded.values()]
    bands = Counter(min(int(w * 10), 9) for w in weights)
    print(f'{len(graded)} (person, species) records, {len(users)} people')
    print('weight distribution: ' + ', '.join(
        f'{b / 10:.1f}-{(b + 1) / 10:.1f}: {bands[b]}' for b in range(10)))
    empty = [(u, verifier_weight(c, n, k), n) for (u, s), (n, c) in graded.items() if s == -1]
    empty.sort(key=lambda r: r[1])
    print('weakest records on Empty (user, weight, n): ' +
          ', '.join(f'{u}:{w:.2f}/{n}' for u, w, n in empty[:5]))


def main():
    apply = '--apply' in sys.argv
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        if not apply:
            with engine.connect() as conn:
                conn.execute(text('SET TRANSACTION READ ONLY'))
                summary(compute(conn), app.config['CAMERA_TRAP_CONFIG'].get('VERIFIER_EXPERIENCE_K', 1))
            print('\nDRY RUN: nothing written. Re-run with --apply.')
            return
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(text(DDL))
        rows = refresh(engine)
        if rows is None:
            print('Another worker is rebuilding the table right now; nothing done.')
            return
        print(f'{TABLE}: {rows} rows written.')
        with engine.connect() as conn:
            graded = {(u, s): (n, c) for u, s, n, c in conn.execute(
                text(f'SELECT user_id, species_id, n, confirmed FROM {TABLE}'))}
        summary(graded, app.config['CAMERA_TRAP_CONFIG'].get('VERIFIER_EXPERIENCE_K', 1))


if __name__ == '__main__':
    main()
