# SPDX-License-Identifier: AGPL-3.0-only
"""Docs: translation sync tracking and minor edits

Revision ID: f4a5b6c7d8e9
Revises: e7f1a2b3c4d5
Create Date: 2026-10-20 18:00:00.000000

Adds `doc_revision.minor` (NOT NULL DEFAULT false: every existing revision
counts as a real change) and `doc_page_translation.synced_with_rev_id` (nullable
FK to doc_revision; NULL = never confirmed, judged by timestamps), and fills the
new column for existing language pairs (baseline: in step as of the migration).
No existing value is changed.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f4a5b6c7d8e9'
down_revision = 'e7f1a2b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('doc_revision', sa.Column('minor', sa.Boolean(), nullable=False,
                                            server_default=sa.false()))
    op.add_column('doc_page_translation', sa.Column('synced_with_rev_id', sa.Integer(),
                                                    nullable=True))
    op.create_foreign_key('fk_doc_translation_synced_rev', 'doc_page_translation',
                          'doc_revision', ['synced_with_rev_id'], ['id'], ondelete='SET NULL')
    # Baseline: the language pairs that exist today count as in step, so tracking
    # starts now instead of flagging every pair imported one language after the
    # other. Fills only the new column (NULL where there is no other language).
    op.execute("""
        UPDATE doc_page_translation AS t
        SET synced_with_rev_id = (
            SELECT max(r.id) FROM doc_revision r
            WHERE r.page_id = t.page_id AND r.lang <> t.lang AND r.body IS NOT NULL
              AND r.action IN ('create', 'edit', 'restore'))
    """)


def downgrade():
    op.drop_constraint('fk_doc_translation_synced_rev', 'doc_page_translation',
                       type_='foreignkey')
    op.drop_column('doc_page_translation', 'synced_with_rev_id')
    op.drop_column('doc_revision', 'minor')
