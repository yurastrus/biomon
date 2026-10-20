# SPDX-License-Identifier: AGPL-3.0-only
"""Docs module (shared-docs): topics, pages, translations, editors, revisions, images

Revision ID: d0c5a1b2c3e4
Revises: b93e7d21c604
Create Date: 2026-10-20 12:00:00.000000

Tables of the docs / manual submodule (app/docs, models.py there). Only new
tables, nothing existing is touched, so the deploy order does not matter.
Seeds the three default topics: camera traps, PAM, general.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd0c5a1b2c3e4'
down_revision = 'b93e7d21c604'
branch_labels = None
depends_on = None


def upgrade():
    topic = op.create_table(
        'doc_topic',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(32), nullable=False, unique=True),
        sa.Column('name_uk', sa.String(100), nullable=False),
        sa.Column('name_en', sa.String(100), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_table(
        'doc_page',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('slug', sa.String(128), nullable=False),
        sa.Column('topic_id', sa.Integer(), sa.ForeignKey('doc_topic.id'), nullable=False),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('doc_page.id'), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('members_only', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('locked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(),
                  sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_doc_page_slug', 'doc_page', ['slug'], unique=True)
    op.create_table(
        'doc_page_translation',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('page_id', sa.Integer(),
                  sa.ForeignKey('doc_page.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lang', sa.String(5), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(),
                  sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('page_id', 'lang', name='uq_doc_page_lang'),
    )
    op.create_table(
        'doc_editor',
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('topic_id', sa.Integer(),
                  sa.ForeignKey('doc_topic.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.Column('granted_by_id', sa.Integer(),
                  sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_table(
        'doc_revision',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('page_id', sa.Integer(),
                  sa.ForeignKey('doc_page.id', ondelete='SET NULL'), nullable=True),
        sa.Column('page_slug', sa.String(128), nullable=False),
        sa.Column('lang', sa.String(5), nullable=True),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('title', sa.String(200), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('note', sa.String(255), nullable=True),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_doc_revision_page_id', 'doc_revision', ['page_id'])
    op.create_index('ix_doc_revision_created_at', 'doc_revision', ['created_at'])
    op.create_table(
        'doc_image',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('filename', sa.String(64), nullable=False, unique=True),
        sa.Column('original_name', sa.String(255), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(),
                  sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.Column('page_id', sa.Integer(),
                  sa.ForeignKey('doc_page.id', ondelete='SET NULL'), nullable=True),
    )

    op.bulk_insert(topic, [
        {'code': 'ct', 'name_uk': 'Фотопастки', 'name_en': 'Camera traps', 'position': 10},
        {'code': 'pam', 'name_uk': 'Акустичний моніторинг (ПАМ)',
         'name_en': 'Acoustic monitoring (PAM)', 'position': 20},
        {'code': 'general', 'name_uk': 'Загальне', 'name_en': 'General', 'position': 30},
    ])


def downgrade():
    op.drop_table('doc_image')
    op.drop_index('ix_doc_revision_created_at', table_name='doc_revision')
    op.drop_index('ix_doc_revision_page_id', table_name='doc_revision')
    op.drop_table('doc_revision')
    op.drop_table('doc_editor')
    op.drop_table('doc_page_translation')
    op.drop_index('ix_doc_page_slug', table_name='doc_page')
    op.drop_table('doc_page')
    op.drop_table('doc_topic')
