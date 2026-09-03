# SPDX-License-Identifier: AGPL-3.0-only
"""Per-module access, phase 4: flags become NOT NULL and can_export is dropped

Revision ID: b93e7d21c604
Revises: a7413fc85d21
Create Date: 2026-09-03 10:00:00.000000

Ends the migration started by a7413fc85d21. Two steps in one revision, because
they only make sense together:

1. the four per-module flags become ``NOT NULL DEFAULT false``. Every row was
   filled by scripts/backfill_module_access.py, so NULL no longer means
   "undecided" — it would only be a bug;
2. ``user_institutions.can_export`` is dropped. Nothing reads it: the export
   paths ask ``can_export_ct`` / ``can_export_pam`` through the module access
   helpers (WORKLOG 2026-09-02, phase 3).

Deploy order matters here, and it is the reverse of the usual one: reload the
app FIRST, then run this migration. A worker still holding the previous code
SELECTs ``can_export`` on every user query, so dropping the column under a
running old worker would break it; the new code never mentions the column, so
dropping it after the reload is safe.

The downgrade is lossless in the direction that matters: ``can_export`` is
recomputed as "may export in either module", which is exactly what it meant.
Per-module detail cannot be represented by it, but nothing that reads the old
column can tell the difference.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b93e7d21c604'
down_revision = 'a7413fc85d21'
branch_labels = None
depends_on = None

TABLE = 'user_institutions'
FLAGS = ('can_view_ct', 'can_export_ct', 'can_view_pam', 'can_export_pam')


def _columns():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade():
    existing = _columns()

    # A NULL here would become false and silently revoke access, so refuse
    # instead of guessing: run the backfill first.
    missing = [name for name in FLAGS if name not in existing]
    if missing:
        raise RuntimeError(
            f'{TABLE} is missing {missing}; run migration a7413fc85d21 first')

    nulls = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE "
        + ' OR '.join(f'{name} IS NULL' for name in FLAGS))).scalar()
    if nulls:
        raise RuntimeError(
            f'{nulls} row(s) in {TABLE} still have an undecided module flag. '
            'Run: python -m scripts.backfill_module_access  (see WORKLOG 2026-09-02)')

    for name in FLAGS:
        op.execute(sa.text(f'UPDATE {TABLE} SET {name} = false WHERE {name} IS NULL'))
        op.alter_column(TABLE, name, existing_type=sa.Boolean(),
                        nullable=False, server_default=sa.false())

    if 'can_export' in existing:
        op.drop_column(TABLE, 'can_export')


def downgrade():
    existing = _columns()

    if 'can_export' not in existing:
        op.add_column(TABLE, sa.Column('can_export', sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
        # "May export in either module" is what the column always meant.
        op.execute(sa.text(
            f'UPDATE {TABLE} SET can_export = '
            '(coalesce(can_export_ct, false) OR coalesce(can_export_pam, false))'))

    for name in FLAGS:
        if name in existing:
            op.alter_column(TABLE, name, existing_type=sa.Boolean(),
                            nullable=True, server_default=None)
