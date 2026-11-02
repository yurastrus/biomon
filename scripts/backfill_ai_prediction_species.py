# SPDX-License-Identifier: AGPL-3.0-only
"""Backfill ai_predictions.prediction_species_id from ai_label_map.

Predictions written before a label got its mapping (DeepFaune, 15-26 May 2026:
cow, equid, bird, mustelid, golden jackal, fallow deer, sheep, goat) kept
prediction_species_id NULL. On /identify the AI hint then shows the raw English
label ("cow") and does not pre-select the species. This fills the id from the
current mapping; rows whose label is still unmapped (undefined, chamois…) stay
NULL. Only NULL rows are touched, so the script is idempotent.

Dry run by default (prints what would change); --apply writes.

    venv/Scripts/python -m scripts.backfill_ai_prediction_species           # dev (tunnel 5433)
    venv/bin/python -m scripts.backfill_ai_prediction_species --apply       # prod
"""
import argparse
import re
from pathlib import Path

import psycopg2

env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
    if m:
        env[m.group(1)] = m.group(2)
URL = env['CT_DATABASE_URL']

PREVIEW = """
SELECT lower(ap.prediction_label), lm.species_id, s.common_name_ua,
       COUNT(*), COUNT(DISTINCT ap.observation_id),
       COUNT(DISTINCT ap.observation_id) FILTER (WHERE o.status = 'pending')
  FROM ai_predictions ap
  JOIN ai_label_map lm ON lm.label = lower(ap.prediction_label)
  JOIN species s ON s.id = lm.species_id
  JOIN observations o ON o.id = ap.observation_id
 WHERE ap.prediction_species_id IS NULL
 GROUP BY 1, 2, 3
 ORDER BY 4 DESC
"""

UPDATE = """
UPDATE ai_predictions ap
   SET prediction_species_id = lm.species_id
  FROM ai_label_map lm
 WHERE ap.prediction_species_id IS NULL
   AND lm.label = lower(ap.prediction_label)
   AND lm.species_id IS NOT NULL
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--apply', action='store_true', help='write the changes')
    args = parser.parse_args()

    conn = psycopg2.connect(URL)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("SET lock_timeout = '5s'")
        cur.execute(PREVIEW)
        rows = cur.fetchall()
        print(f'{"label":15} {"species":>7}  {"name":22} {"rows":>7} {"series":>7} {"pending":>7}')
        for label, sid, ua, n, obs, pend in rows:
            print(f'{label:15} {sid:>7}  {ua or "":22} {n:>7} {obs:>7} {pend:>7}')
        print(f'total rows: {sum(r[3] for r in rows)}')
        if not args.apply:
            print('dry run, nothing written (use --apply)')
            conn.rollback()
            return
        cur.execute(UPDATE)
        print(f'updated: {cur.rowcount}')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
