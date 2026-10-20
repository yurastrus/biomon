BEGIN;

-- Running upgrade b93e7d21c604 -> d0c5a1b2c3e4

CREATE TABLE doc_topic (
    id SERIAL NOT NULL, 
    code VARCHAR(32) NOT NULL, 
    name_uk VARCHAR(100) NOT NULL, 
    name_en VARCHAR(100), 
    position INTEGER DEFAULT '0' NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (code)
);

CREATE TABLE doc_page (
    id SERIAL NOT NULL, 
    slug VARCHAR(128) NOT NULL, 
    topic_id INTEGER NOT NULL, 
    parent_id INTEGER, 
    position INTEGER DEFAULT '0' NOT NULL, 
    published BOOLEAN DEFAULT true NOT NULL, 
    members_only BOOLEAN DEFAULT false NOT NULL, 
    locked BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    created_by_id INTEGER, 
    PRIMARY KEY (id), 
    FOREIGN KEY(topic_id) REFERENCES doc_topic (id), 
    FOREIGN KEY(parent_id) REFERENCES doc_page (id), 
    FOREIGN KEY(created_by_id) REFERENCES "user" (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX ix_doc_page_slug ON doc_page (slug);

CREATE TABLE doc_page_translation (
    id SERIAL NOT NULL, 
    page_id INTEGER NOT NULL, 
    lang VARCHAR(5) NOT NULL, 
    title VARCHAR(200) NOT NULL, 
    body TEXT NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    updated_by_id INTEGER, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_doc_page_lang UNIQUE (page_id, lang), 
    FOREIGN KEY(page_id) REFERENCES doc_page (id) ON DELETE CASCADE, 
    FOREIGN KEY(updated_by_id) REFERENCES "user" (id) ON DELETE SET NULL
);

CREATE TABLE doc_editor (
    user_id INTEGER NOT NULL, 
    topic_id INTEGER NOT NULL, 
    granted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    granted_by_id INTEGER, 
    PRIMARY KEY (user_id, topic_id), 
    FOREIGN KEY(user_id) REFERENCES "user" (id) ON DELETE CASCADE, 
    FOREIGN KEY(topic_id) REFERENCES doc_topic (id) ON DELETE CASCADE, 
    FOREIGN KEY(granted_by_id) REFERENCES "user" (id) ON DELETE SET NULL
);

CREATE TABLE doc_revision (
    id SERIAL NOT NULL, 
    page_id INTEGER, 
    page_slug VARCHAR(128) NOT NULL, 
    lang VARCHAR(5), 
    action VARCHAR(16) NOT NULL, 
    title VARCHAR(200), 
    body TEXT, 
    note VARCHAR(255), 
    user_id INTEGER, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(page_id) REFERENCES doc_page (id) ON DELETE SET NULL, 
    FOREIGN KEY(user_id) REFERENCES "user" (id) ON DELETE SET NULL
);

CREATE INDEX ix_doc_revision_page_id ON doc_revision (page_id);

CREATE INDEX ix_doc_revision_created_at ON doc_revision (created_at);

CREATE TABLE doc_image (
    id SERIAL NOT NULL, 
    filename VARCHAR(64) NOT NULL, 
    original_name VARCHAR(255), 
    width INTEGER, 
    height INTEGER, 
    size_bytes INTEGER, 
    uploaded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    uploaded_by_id INTEGER, 
    page_id INTEGER, 
    PRIMARY KEY (id), 
    UNIQUE (filename), 
    FOREIGN KEY(uploaded_by_id) REFERENCES "user" (id) ON DELETE SET NULL, 
    FOREIGN KEY(page_id) REFERENCES doc_page (id) ON DELETE SET NULL
);

INSERT INTO doc_topic (code, name_uk, name_en, position) VALUES ('ct', 'Фотопастки', 'Camera traps', 10);

INSERT INTO doc_topic (code, name_uk, name_en, position) VALUES ('pam', 'Акустичний моніторинг (ПАМ)', 'Acoustic monitoring (PAM)', 20);

INSERT INTO doc_topic (code, name_uk, name_en, position) VALUES ('general', 'Загальне', 'General', 30);

UPDATE alembic_version SET version_num='d0c5a1b2c3e4' WHERE alembic_version.version_num = 'b93e7d21c604';

COMMIT;

