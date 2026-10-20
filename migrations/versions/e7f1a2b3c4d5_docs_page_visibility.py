# SPDX-License-Identifier: AGPL-3.0-only
"""Docs pages: visibility for selected roles or for editors only

Revision ID: e7f1a2b3c4d5
Revises: d0c5a1b2c3e4
Create Date: 2026-10-20 14:00:00.000000

Adds `doc_page.editors_only` (NOT NULL DEFAULT false, so existing pages keep
their visibility) and the link table `doc_page_roles` (page -> host role).
Only additions; nothing existing is changed or removed.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7f1a2b3c4d5'
down_revision = 'd0c5a1b2c3e4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('doc_page', sa.Column('editors_only', sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.create_table(
        'doc_page_roles',
        sa.Column('page_id', sa.Integer(),
                  sa.ForeignKey('doc_page.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role_id', sa.Integer(),
                  sa.ForeignKey('role.id', ondelete='CASCADE'), primary_key=True),
    )


def downgrade():
    op.drop_table('doc_page_roles')
    op.drop_column('doc_page', 'editors_only')
