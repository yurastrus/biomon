# SPDX-License-Identifier: AGPL-3.0-only
"""Fill the per-module institution access columns from what people have today.

Phase 1 of splitting `user_institutions` per module (see WORKLOG 2026-09-02).
The site still reads the legacy columns, so running this changes nothing about
who can do what right now — it only records, per row, what the current access
means in the new four-flag shape.

Rules (as agreed with the project owner)
----------------------------------------
For every existing ``user_institutions`` row:

* **camera traps** — access ON. The row itself was the access grant, and camera
  traps is the module every such grant covered.
* **camera-trap export** — copies ``can_export``.
* **PAM** — access ON only if the person may verify sounds at all, that is holds
  ``pam_verifier`` (managers and admins hold it through the role hierarchy).
  Somebody who only ever had ``ct_verifier`` gets no PAM access.
* **PAM export** — ON only where the person both gets PAM access here and
  already had ``can_export`` on this institution. Nobody gains an export right
  they did not have.

Because the columns are nullable, "never decided" (NULL) is distinguishable from
a deliberate False. By default only rows with an undecided flag are touched, so
the script is safe to re-run right before the read paths are switched over —
including on rows created after the first run by code that predates the columns.

Usage
-----
    venv/Scripts/python -m scripts.backfill_module_access --dry-run   # report only
    venv/Scripts/python -m scripts.backfill_module_access             # fill NULLs
    venv/Scripts/python -m scripts.backfill_module_access --recompute # also overwrite
                                                                      # decided rows

``--recompute`` exists for a rollback of a bad manual edit; it discards choices
made in the admin form, so it is never the default.

A run writes a timestamped report to ./logs/.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path


def target_flags(*, can_export, may_verify_pam):
    """The four flags one legacy row translates into.

    Pure function, no DB: this is the rule that the tests pin.

    Args:
        can_export: value of the row's legacy ``can_export``.
        may_verify_pam: whether the person holds ``pam_verifier`` (hierarchy
            included, so managers and admins count).

    Returns:
        dict[str, bool]
    """
    can_export = bool(can_export)
    may_verify_pam = bool(may_verify_pam)
    return {
        'can_view_ct': True,
        'can_export_ct': can_export,
        'can_view_pam': may_verify_pam,
        'can_export_pam': may_verify_pam and can_export,
    }


FLAG_NAMES = ('can_view_ct', 'can_export_ct', 'can_view_pam', 'can_export_pam')


def plan_row(link, user, recompute=False):
    """Return the changes for one row, or an empty dict if nothing to do."""
    wanted = target_flags(can_export=link.can_export,
                          may_verify_pam=user.has_role('pam_verifier'))
    changes = {}
    for name in FLAG_NAMES:
        current = getattr(link, name)
        if current is None or recompute:
            if current != wanted[name]:
                changes[name] = wanted[name]
    return changes


def run(dry_run=False, recompute=False, echo=print):
    from app import create_app
    from app.extensions import db
    from app.models import User, UserInstitution

    app = create_app()
    with app.app_context():
        links = UserInstitution.query.order_by(
            UserInstitution.user_id, UserInstitution.institution_id).all()
        users = {u.id: u for u in User.query.all()}

        touched = 0
        pam_on = pam_off = export_pam_on = 0
        lines = []

        for link in links:
            user = users.get(link.user_id)
            if user is None:            # orphan row; leave it for a human
                echo(f'  ! row user_id={link.user_id} has no user, skipped')
                continue
            changes = plan_row(link, user, recompute=recompute)
            wanted = target_flags(can_export=link.can_export,
                                  may_verify_pam=user.has_role('pam_verifier'))
            if wanted['can_view_pam']:
                pam_on += 1
            else:
                pam_off += 1
            if wanted['can_export_pam']:
                export_pam_on += 1
            if not changes:
                continue
            touched += 1
            inst = link.institution
            lines.append(
                f'  {user.username:24} {getattr(inst, "code", None) or link.institution_id:8} '
                + ' '.join(f'{k.replace("can_", "")}={v}' for k, v in changes.items()))
            if not dry_run:
                for name, value in changes.items():
                    setattr(link, name, value)

        summary = (
            f'rows total={len(links)} changed={touched} '
            f'(pam access on={pam_on} off={pam_off}, pam export on={export_pam_on})')
        for line in lines:
            echo(line)
        echo(summary)

        if dry_run:
            db.session.rollback()
            echo('dry run: nothing written')
        else:
            db.session.commit()
            echo('committed')

        _write_report(lines, summary, dry_run=dry_run, recompute=recompute)
        return touched


def _write_report(lines, summary, dry_run, recompute):
    """Keep a copy of what a run did; access grants deserve a paper trail."""
    logs = Path(__file__).resolve().parent.parent / 'logs'
    try:
        logs.mkdir(exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        mode = 'dryrun' if dry_run else ('recompute' if recompute else 'fill')
        path = logs / f'backfill_module_access_{mode}_{stamp}.log'
        path.write_text('\n'.join(lines + [summary]) + '\n', encoding='utf-8')
        print(f'report: {path}')
    except Exception as e:                              # never fail the run over a log
        print(f'could not write report: {e}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true',
                        help='report the changes without writing them')
    parser.add_argument('--recompute', action='store_true',
                        help='also overwrite flags that were already decided')
    args = parser.parse_args()
    run(dry_run=args.dry_run, recompute=args.recompute)
    return 0


if __name__ == '__main__':
    sys.exit(main())
