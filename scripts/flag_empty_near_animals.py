# SPDX-License-Identifier: AGPL-3.0-only
"""Flag 'Empty' series closed by one person + DeepFaune that sit next to an
animal series at the same location (2026-11-23).

The weighted recount closed 11,961 pending series as Empty on one very
reliable person plus DeepFaune; the nightly cleanup archives them. No AI
signal separates the doubtful ones (every frame of every model said empty,
no detector boxes), so the only lead is time: an empty series shot within
WINDOW minutes of a series with an animal (by people or by DeepFaune) at the
same location may be the tail of that animal's visit. Flagged series are
kept by the cleanup until an admin removes the flag (/admin/flagged).

Run from the project root (DRY RUN by default):
    venv/Scripts/python -m scripts.flag_empty_near_animals [--minutes 15] [--apply]
"""
import json
import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine

AUTHOR = 'weighted-consensus-audit'
PREFIX = 'ct-flag-author-v1:'   # routes._FLAG_AUTHOR_PREFIX

CANDIDATES_SQL = """
WITH rec AS (
    SELECT p.observation_id obs
    FROM identifications i JOIN photos p ON p.id = i.photo_id
    JOIN observations o ON o.id = p.observation_id
    WHERE o.status = 'completed' AND NOT o.flagged
    GROUP BY p.observation_id
    HAVING COUNT(DISTINCT i.user_id) = 1 AND MIN(i.species_id) = -1 AND MAX(i.species_id) = -1),
animal_obs AS (
    SELECT DISTINCT p.observation_id obs FROM identifications i JOIN photos p ON p.id = i.photo_id
    WHERE i.species_id > 0
    UNION
    SELECT DISTINCT observation_id FROM ai_predictions WHERE prediction_species_id > 0)
SELECT DISTINCT r.obs
FROM rec r JOIN observations o ON o.id = r.obs
JOIN observations o2 ON o2.location_id = o.location_id AND o2.id <> o.id
     AND o2.series_start_time BETWEEN o.series_start_time - make_interval(mins => :m)
                                  AND o.series_end_time + make_interval(mins => :m)
JOIN animal_obs a ON a.obs = o2.id
ORDER BY r.obs
"""


def main():
    apply = '--apply' in sys.argv
    minutes = 15
    if '--minutes' in sys.argv:
        minutes = int(sys.argv[sys.argv.index('--minutes') + 1])
    note = (f'Auto: Empty closed by one person + DeepFaune (weighted recount 2026-11-23); '
            f'an animal series is within {minutes} min at the same location. Check before '
            f'removing the flag.')
    flag_note = f'{PREFIX}{json.dumps(AUTHOR)}\n{note}'
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        with engine.begin() as conn:
            ids = [r[0] for r in conn.execute(text(CANDIDATES_SQL), {'m': minutes})]
            print(f'{len(ids)} series within {minutes} min of an animal series: {ids}')
            if not apply:
                print('DRY RUN: nothing flagged. Re-run with --apply.')
                return
            if ids:
                conn.execute(text("""UPDATE observations SET flagged = TRUE, flag_note = :n
                                     WHERE id = ANY(:ids) AND NOT flagged"""),
                             {'n': flag_note, 'ids': ids})
            print(f'flagged {len(ids)} series')


if __name__ == '__main__':
    main()
