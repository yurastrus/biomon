"""Add contact_submissions table

Revision ID: b7d2e1a9c4f0
Revises: c05253cd0c2b
Create Date: 2026-06-25 16:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d2e1a9c4f0'
down_revision = 'c05253cd0c2b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'contact_submissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='new'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('contact_submissions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_contact_submissions_submitted_at'),
            ['submitted_at'], unique=False)


def downgrade():
    with op.batch_alter_table('contact_submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_submissions_submitted_at'))
    op.drop_table('contact_submissions')
