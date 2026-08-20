# SPDX-License-Identifier: AGPL-3.0-only
"""Per-user email notification opt-outs

Revision ID: e5c31b8a7d94
Revises: d41a7c9e5b02
Create Date: 2026-08-20 12:00:00.000000

Notes
-----
* ``notify_ct_pending`` gets ``server_default=true`` so every existing account
  stays subscribed to the weekly camera-trap reminder — the column is an
  opt-OUT, and a migration must not unsubscribe anybody.
* The PAM equivalent is deliberately not created here; it lands with the PAM
  digest itself (see app/utils/notification_prefs.py).
"""
from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5c31b8a7d94'
down_revision = 'd41a7c9e5b02'
branch_labels = None
depends_on = None


def upgrade():
    # The existence check needs a live connection; in offline mode (`--sql`)
    # there is none, and emitting the ADD COLUMN unconditionally is the right
    # output for a human-reviewed script anyway.
    if not context.is_offline_mode():
        existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('user')}
        if 'notify_ct_pending' in existing:
            return  # already applied outside Alembic
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notify_ct_pending', sa.Boolean(),
                                      nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('notify_ct_pending')
