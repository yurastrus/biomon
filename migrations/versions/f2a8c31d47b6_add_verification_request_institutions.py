# SPDX-License-Identifier: AGPL-3.0-only
"""Registration: requested institutions + the applicant's own note

Revision ID: f2a8c31d47b6
Revises: e5c31b8a7d94
Create Date: 2026-09-02 10:00:00.000000

One row per (verification request, institution). Each row carries its own
status, because an applicant naming two institutions needs a decision from each
institution's manager and the request stays pending until every row is answered.

Also adds ``verification_requests.applicant_note`` — the free text the applicant
writes about their motivation and experience, which the decider reads before
granting anything.

Idempotent: skips whatever already exists, so a database that acquired either
outside Alembic is left alone.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a8c31d47b6'
down_revision = 'e5c31b8a7d94'
branch_labels = None
depends_on = None

TABLE = 'verification_request_institutions'


def _has_applicant_note():
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns(
        'verification_requests')}
    return 'applicant_note' in cols


def upgrade():
    if not _has_applicant_note():
        op.add_column('verification_requests',
                      sa.Column('applicant_note', sa.Text(), nullable=True))

    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('institution_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False,
                  server_default='pending'),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['request_id'], ['verification_requests.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['institution_id'], ['institutions.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_id'], ['user.id']),
        sa.UniqueConstraint('request_id', 'institution_id',
                            name='uq_verification_request_institution'),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')",
                           name='ck_verification_request_institution_status'),
    )
    op.create_index(op.f(f'ix_{TABLE}_request_id'), TABLE, ['request_id'])
    op.create_index(op.f(f'ix_{TABLE}_institution_id'), TABLE, ['institution_id'])
    op.create_index(op.f(f'ix_{TABLE}_status'), TABLE, ['status'])


def downgrade():
    if _has_applicant_note():
        op.drop_column('verification_requests', 'applicant_note')
    if TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index(op.f(f'ix_{TABLE}_status'), table_name=TABLE)
    op.drop_index(op.f(f'ix_{TABLE}_institution_id'), table_name=TABLE)
    op.drop_index(op.f(f'ix_{TABLE}_request_id'), table_name=TABLE)
    op.drop_table(TABLE)
