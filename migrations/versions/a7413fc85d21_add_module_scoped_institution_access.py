# SPDX-License-Identifier: AGPL-3.0-only
"""Per-module institution access: user_institutions.can_{view,export}_{ct,pam}

Revision ID: a7413fc85d21
Revises: f2a8c31d47b6
Create Date: 2026-09-02 18:30:00.000000

Phase 1 of splitting institution access per module. The legacy columns are left
exactly as they are: a row still means "sees this institution" and ``can_export``
still means "may download its data", and every read path in shared-ct /
shared-pam keeps using them until a later phase switches over. This migration
only makes the finer grants storable, so nothing changes for the running site.

The new columns are NULLABLE on purpose. NULL means "never decided", which is
what lets scripts/backfill_module_access.py fill rows written by code that
predates the columns without ever overwriting a choice made in the admin form.
A NULL also keeps a row readable through UserInstitution.module_flags(), which
falls back to the legacy meaning.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7413fc85d21'
down_revision = 'f2a8c31d47b6'
branch_labels = None
depends_on = None

TABLE = 'user_institutions'
COLUMNS = ('can_view_ct', 'can_export_ct', 'can_view_pam', 'can_export_pam')


def _existing():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade():
    existing = _existing()
    for name in COLUMNS:
        if name not in existing:
            op.add_column(TABLE, sa.Column(name, sa.Boolean(), nullable=True))


def downgrade():
    existing = _existing()
    for name in reversed(COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
