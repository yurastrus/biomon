"""Idempotent migration: AI detector level lookup table + ai_models.level_id.

Creates ai_model_levels, seeds the 4 known levels (DF, MDS, DF+MDS, MDR),
adds the ai_models.level_id FK column, replaces the unique constraint with
(name, version, level_id), and tags the existing DeepFaune row with its level
based on detector from config_json.

Run:
    venv/Scripts/python -m scripts.init_model_levels   # Windows/dev (via tunnel 5433)
    venv/bin/python -m scripts.init_model_levels       # Linux/prod

Safe to run multiple times.
"""
import re
from pathlib import Path
import psycopg2

env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
    if m:
        env[m.group(1)] = m.group(2)
URL = env['CT_DATABASE_URL']

LEVELS = [
    # code,     name,                                  detector,                                       rank
    ('DF',      'DeepFaune YOLOv8s (швидкий)',         'deepfaune-yolov8s_960',                        10),
    ('MDS',     'MegaDetector Sorrel (середній)',      'md_v1000.0.0-sorrel',                          20),
    ('DF+MDS',  'DF + MDS ensemble',                   'deepfaune-yolov8s_960 + md_v1000.0.0-sorrel',  30),
    ('MDR',     'MegaDetector Redwood (точний)',       'md_v1000.0.0-redwood',                         40),
]

DDL = """
CREATE TABLE IF NOT EXISTS ai_model_levels (
    id            SERIAL PRIMARY KEY,
    code          VARCHAR(32)  NOT NULL UNIQUE,
    name          VARCHAR(128) NOT NULL,
    detector      VARCHAR(128),
    accuracy_rank INTEGER      NOT NULL DEFAULT 0,
    description   TEXT,
    created_at    TIMESTAMP    NOT NULL DEFAULT now()
);

ALTER TABLE ai_models
    ADD COLUMN IF NOT EXISTS level_id INTEGER REFERENCES ai_model_levels(id);
"""

SWAP_CONSTRAINT = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ai_models_name_version') THEN
        ALTER TABLE ai_models DROP CONSTRAINT uq_ai_models_name_version;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_ai_models_name_version_level') THEN
        ALTER TABLE ai_models
            ADD CONSTRAINT uq_ai_models_name_version_level UNIQUE (name, version, level_id);
    END IF;
END $$;
"""

SEED = """
INSERT INTO ai_model_levels (code, name, detector, accuracy_rank)
VALUES (%s, %s, %s, %s)
ON CONFLICT (code) DO UPDATE
    SET name = EXCLUDED.name,
        detector = EXCLUDED.detector,
        accuracy_rank = EXCLUDED.accuracy_rank;
"""


def main():
    conn = psycopg2.connect(URL)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(DDL)
        for row in LEVELS:
            cur.execute(SEED, row)
        cur.execute(SWAP_CONSTRAINT)

        # Tag existing ai_models rows with their level based on detector from config_json.
        cur.execute("""
            UPDATE ai_models m
               SET level_id = l.id
              FROM ai_model_levels l
             WHERE m.level_id IS NULL
               AND m.config_json ->> 'detector' = l.detector
        """)
        tagged = cur.rowcount

        conn.commit()

        cur.execute("SELECT id, code, name, detector, accuracy_rank FROM ai_model_levels ORDER BY accuracy_rank")
        print('=== ai_model_levels ===')
        for r in cur.fetchall():
            print('  ', r)
        cur.execute("""
            SELECT m.id, m.name, m.version, m.is_active, l.code AS level
              FROM ai_models m LEFT JOIN ai_model_levels l ON l.id = m.level_id
             ORDER BY m.id
        """)
        print('=== ai_models ===')
        for r in cur.fetchall():
            print('  ', r)
        print(f'[done] tagged existing ai_models rows: {tagged}')
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
