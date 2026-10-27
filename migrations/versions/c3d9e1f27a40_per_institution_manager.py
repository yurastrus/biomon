# SPDX-License-Identifier: AGPL-3.0-only
"""Per-institution manager rights: user_institutions.can_manage_{ct,pam}

Revision ID: c3d9e1f27a40
Revises: f4a5b6c7d8e9
Create Date: 2026-10-27 21:10:00.000000

Until now the global ``manager`` role made a person manager of every institution
they could see. The role stays as the gate; these two flags say where it applies.

Seeding keeps the running site exactly as it was: every current holder of the
``manager`` role gets ``can_manage_<module> = can_view_<module>`` on each of their
rows, i.e. they keep managing everything they managed. An admin then narrows it
in the user form. Rows of people without the role stay false.

Run BEFORE the code that reads the columns (the model declares them).
Idempotent: columns are added only if missing; the seed only runs when they were
just added, so a re-run never overwrites choices made in the admin form.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d9e1f27a40'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None

TABLE = 'user_institutions'
COLUMNS = ('can_manage_ct', 'can_manage_pam')


def _existing():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def seed_sql(columns=COLUMNS):
    """The seeding UPDATE, plain SQL that runs on PostgreSQL and SQLite alike.

    Exposed so scripts/audit_manager_scope.py can replay exactly this statement
    on a copy of the real rows before the migration is run on production.
    """
    sets = ', '.join(f"{name} = can_view_{name.rsplit('_', 1)[1]}" for name in columns)
    return f"""
        UPDATE {TABLE} SET {sets}
        WHERE EXISTS (
            SELECT 1 FROM user_roles ur JOIN role r ON r.id = ur.role_id
            WHERE ur.user_id = {TABLE}.user_id AND r.name = 'manager'
        )
    """


def upgrade():
    existing = _existing()
    added = [name for name in COLUMNS if name not in existing]
    for name in added:
        op.add_column(TABLE, sa.Column(name, sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    if added:
        op.execute(seed_sql(added))


def downgrade():
    existing = _existing()
    for name in reversed(COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
