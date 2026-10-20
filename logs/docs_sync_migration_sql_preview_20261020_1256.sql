BEGIN;

-- Running upgrade e7f1a2b3c4d5 -> f4a5b6c7d8e9

ALTER TABLE doc_revision ADD COLUMN minor BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE doc_page_translation ADD COLUMN synced_with_rev_id INTEGER;

ALTER TABLE doc_page_translation ADD CONSTRAINT fk_doc_translation_synced_rev FOREIGN KEY(synced_with_rev_id) REFERENCES doc_revision (id) ON DELETE SET NULL;

UPDATE doc_page_translation AS t
        SET synced_with_rev_id = (
            SELECT max(r.id) FROM doc_revision r
            WHERE r.page_id = t.page_id AND r.lang <> t.lang AND r.body IS NOT NULL
              AND r.action IN ('create', 'edit', 'restore'));

UPDATE alembic_version SET version_num='f4a5b6c7d8e9' WHERE alembic_version.version_num = 'e7f1a2b3c4d5';

COMMIT;

