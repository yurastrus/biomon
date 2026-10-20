BEGIN;

-- Running upgrade d0c5a1b2c3e4 -> e7f1a2b3c4d5

ALTER TABLE doc_page ADD COLUMN editors_only BOOLEAN DEFAULT false NOT NULL;

CREATE TABLE doc_page_roles (
    page_id INTEGER NOT NULL, 
    role_id INTEGER NOT NULL, 
    PRIMARY KEY (page_id, role_id), 
    FOREIGN KEY(page_id) REFERENCES doc_page (id) ON DELETE CASCADE, 
    FOREIGN KEY(role_id) REFERENCES role (id) ON DELETE CASCADE
);

UPDATE alembic_version SET version_num='e7f1a2b3c4d5' WHERE alembic_version.version_num = 'd0c5a1b2c3e4';

COMMIT;

