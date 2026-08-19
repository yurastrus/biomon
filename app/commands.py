# SPDX-License-Identifier: AGPL-3.0-only
"""Flask CLI commands for administrative tasks.

Registration: called from app/__init__.py via register_commands(app).
"""
import os
import sys
from pathlib import Path

import click


def register_commands(app):

    @app.cli.command('send-id-reminders')
    def send_id_reminders():
        """Send weekly email reminders about unidentified observation series.

        Usage:
            flask send-id-reminders

        Cron schedule (every Monday at 09:00):
            0 9 * * 1 cd /var/www/biomon && venv/bin/flask send-id-reminders >> /var/log/biomon_reminders.log 2>&1
        """
        from app.camera_traps.notifications import send_identification_reminders
        click.echo("Checking unidentified series and sending reminders...")
        sent, skipped = send_identification_reminders()
        click.echo(f"Done: {sent} emails sent, {skipped} users skipped (no series).")

    @app.cli.command('purge-unconfirmed')
    @click.option('--days', default=None, type=int,
                  help='Age threshold in days (default: UNCONFIRMED_ACCOUNT_MAX_AGE_DAYS).')
    @click.option('--dry-run', is_flag=True, help='Only report what would be deleted.')
    def purge_unconfirmed(days, dry_run):
        """Delete self-registered accounts that never confirmed their email.

        Usage:
            flask purge-unconfirmed [--days 7] [--dry-run]

        Cron schedule (daily at 04:30):
            30 4 * * * cd /var/www/biomon && venv/bin/flask purge-unconfirmed >> /var/log/biomon_purge.log 2>&1
        """
        from datetime import datetime, timedelta
        from app.models import User
        from app.utils.registration import purge_unconfirmed_users

        max_age = days if days is not None else app.config['UNCONFIRMED_ACCOUNT_MAX_AGE_DAYS']
        if dry_run:
            cutoff = datetime.utcnow() - timedelta(days=max_age)
            stale = User.query.filter(
                User.self_registered.is_(True),
                User.email_confirmed_at.is_(None),
                User.created_at < cutoff,
            ).all()
            for u in stale:
                click.echo(f"would delete: {u.username} <{u.email}> created {u.created_at}")
            click.echo(f"{len(stale)} account(s) would be deleted (older than {max_age} d).")
            return
        deleted = purge_unconfirmed_users(max_age_days=max_age)
        click.echo(f"Deleted {deleted} unconfirmed account(s) older than {max_age} days.")

    # ──────────────────────────────────────────────────────────────
    # SDM CLI commands (flask sdm check / build-grid / ...)
    # ──────────────────────────────────────────────────────────────
    #
    # shared-sdm is attached as a git submodule at app/sdm/, just like
    # app/camera_traps/ (shared-ct) and app/pam/ (shared-pam).
    #
    # app/sdm/__init__.py adds itself to sys.path when the blueprint is
    # imported, but commands.py may run before that (in flask CLI) —
    # so we duplicate the path insertion here for safety.
    _shared_sdm_root = Path(__file__).resolve().parent / "sdm"
    if _shared_sdm_root.is_dir() and str(_shared_sdm_root) not in sys.path:
        sys.path.insert(0, str(_shared_sdm_root))

    try:
        from adapters.biomon_cli import register as register_sdm_cli
        register_sdm_cli(app)
    except ImportError as e:
        # Don't crash the entire app if shared-sdm is not available yet.
        app.logger.warning(
            "SDM CLI not registered (%s). "
            "Check that shared-sdm is located next to biomon.",
            e,
        )
