# SPDX-License-Identifier: AGPL-3.0-only
"""Self-service registration: user activation/confirmation fields + verification_requests

Revision ID: d41a7c9e5b02
Revises: b7d2e1a9c4f0
Create Date: 2026-08-19 15:40:00.000000

Notes
-----
* ``user.email`` becomes UNIQUE. Existing rows may hold NULL (allowed — NULLs are
  distinct in both PostgreSQL and SQLite unique indexes) but must not hold
  duplicates. Rather than silently mangling data, the upgrade aborts with the
  offending addresses listed so a human can decide what to merge.
* ``is_active`` / ``self_registered`` get server defaults so existing accounts
  stay exactly as they are (active, admin-created).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd41a7c9e5b02'
down_revision = 'b7d2e1a9c4f0'
branch_labels = None
depends_on = None


def _existing_user_columns():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('user')}


def _backfill_columns_missing_from_the_chain(existing):
    """Add `user` columns the model has but no migration ever created.

    The chain up to b7d2e1a9c4f0 creates `user` without email / phone /
    created_at / created_by_id — production acquired them outside Alembic, so
    `flask db upgrade` on an empty database produced a schema that did not match
    the model. This migration needs two of them (email for the unique index,
    created_at for the unconfirmed-account purge), so it creates whatever is
    absent. A no-op on any database that already has them.
    """
    to_add = []
    if 'email' not in existing:
        to_add.append(sa.Column('email', sa.String(length=120), nullable=True))
    if 'created_at' not in existing:
        to_add.append(sa.Column('created_at', sa.DateTime(), nullable=True))
    if not to_add:
        return
    print('NOTE: adding user columns missing from the migration chain: '
          + ', '.join(c.name for c in to_add))
    with op.batch_alter_table('user', schema=None) as batch_op:
        for col in to_add:
            batch_op.add_column(col)
    if any(c.name == 'email' for c in to_add):
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_user_email'), ['email'], unique=False)


def _abort_on_duplicate_emails():
    """Fail loudly (before any DDL) if user.email cannot become UNIQUE."""
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT email, COUNT(*) AS n
        FROM "user"
        WHERE email IS NOT NULL AND email <> ''
        GROUP BY email
        HAVING COUNT(*) > 1
    """)).fetchall()
    if rows:
        listed = ', '.join(f'{r[0]} (x{r[1]})' for r in rows)
        raise RuntimeError(
            "Cannot make user.email UNIQUE — these addresses are used by more "
            f"than one account: {listed}. Resolve the duplicates, then re-run "
            "the migration."
        )


def upgrade():
    existing = _existing_user_columns()
    _backfill_columns_missing_from_the_chain(existing)
    if 'email' in existing:
        _abort_on_duplicate_emails()

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=False,
                                      server_default=sa.true()))
        batch_op.add_column(sa.Column('email_confirmed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('self_registered', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
        batch_op.add_column(sa.Column('locale', sa.String(length=5), nullable=True))
        batch_op.create_index('uq_user_email', ['email'], unique=True)

    op.create_table(
        'verification_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('module', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False,
                  server_default='pending'),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('decided_by_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'module',
                            name='uq_verification_request_user_module'),
        sa.CheckConstraint("module IN ('ct', 'pam')",
                           name='ck_verification_request_module'),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')",
                           name='ck_verification_request_status'),
    )
    with op.batch_alter_table('verification_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_verification_requests_user_id'),
                              ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_verification_requests_status'),
                              ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('verification_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_verification_requests_status'))
        batch_op.drop_index(batch_op.f('ix_verification_requests_user_id'))
    op.drop_table('verification_requests')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index('uq_user_email')
        batch_op.drop_column('locale')
        batch_op.drop_column('self_registered')
        batch_op.drop_column('email_confirmed_at')
        batch_op.drop_column('is_active')
