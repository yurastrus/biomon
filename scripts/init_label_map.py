"""Idempotent migration: ai_label_map table (DeepFaune label → species_id).

Creates the ai_label_map table and seeds it from the embedded dictionary
services/biomon_ai/species_map.DEEPFAUNE_TO_SPECIES_ID — so the worker and
the import page share a single source of truth in the DB.

The seed does NOT overwrite existing rows (ON CONFLICT DO NOTHING) — manual
edits to the mapping in the DB survive a re-run.

Run:
    venv/Scripts/python -m scripts.init_label_map   # Windows/dev (tunnel 5433)
    venv/bin/python -m scripts.init_label_map       # Linux/prod
"""
import re
from pathlib import Path
import psycopg2

from services.biomon_ai.species_map import DEEPFAUNE_TO_SPECIES_ID

env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
    if m:
        env[m.group(1)] = m.group(2)
URL = env['CT_DATABASE_URL']

DDL = """
CREATE TABLE IF NOT EXISTS ai_label_map (
    label      VARCHAR(64) PRIMARY KEY,
    species_id INTEGER REFERENCES species(id),
    note       TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
"""

SEED = """
INSERT INTO ai_label_map (label, species_id)
VALUES (%s, %s)
ON CONFLICT (label) DO NOTHING;
"""


def main():
    conn = psycopg2.connect(URL)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(DDL)
        for label, sid in DEEPFAUNE_TO_SPECIES_ID.items():
            cur.execute(SEED, (label.strip().lower(), sid))
        conn.commit()

        cur.execute("""
            SELECT m.label, m.species_id, s.common_name_ua
              FROM ai_label_map m
              LEFT JOIN species s ON s.id = m.species_id
             ORDER BY (m.species_id IS NULL), m.label
        """)
        rows = cur.fetchall()
        print(f'=== ai_label_map: {len(rows)} rows ===')
        for label, sid, ua in rows:
            print(f'  {label:20} -> {("NULL" if sid is None else str(sid)):>5}  {ua or ""}')
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
