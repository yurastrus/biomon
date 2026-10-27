# SPDX-License-Identifier: AGPL-3.0-only
"""Command-line twin of /admin/access-review (logic in app/admin/access_review.py).

Proposes, and with --apply removes, a right (export or access) in one module for
people registered longer than --months who verified fewer than --min-units units
on an institution's locations. Used on 2026-10-27 for the first cuts (camera-trap
export: 29 rows, PAM export: 23 rows; excluded dbu, daria.svidzinska,
bohdan.datsko). Kept under its first name so the WORKLOG and logs still match.

Usage:
    venv/Scripts/python -m scripts.trim_ct_export --module pam --right export \\
        --exclude dbu [--months 6] [--min-units 50] [--json out.json] [--apply]

Dry run by default. --apply commits only the proposed rows.
"""
import argparse
import json
import sys
import warnings

warnings.filterwarnings('ignore')

from app import create_app  # noqa: E402
from app.admin import access_review as ar  # noqa: E402
from app.extensions import db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module', choices=ar.MODULES, default='ct')
    ap.add_argument('--right', choices=ar.RIGHTS, default='export')
    ap.add_argument('--direction', choices=ar.DIRECTIONS, default='revoke')
    ap.add_argument('--months', type=int, default=ar.DEFAULT_MONTHS)
    ap.add_argument('--min-units', type=int, default=ar.DEFAULT_MIN_UNITS)
    ap.add_argument('--exclude', action='append', default=[],
                    help='username to leave untouched (repeatable)')
    ap.add_argument('--json')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        params = dict(module=args.module, right=args.right, months=args.months,
                      min_units=args.min_units, exclude=args.exclude,
                      direction=args.direction)
        data = ar.propose(**params)
        print(f"module: {args.module}, right: {args.right}, registered before "
              f"{data['cutoff']:%Y-%m-%d}, min units: {args.min_units}")
        people = {i['user_id'] for i in data['items']}
        print(f"{args.direction:14} rows {len(data['items']):4}  people {len(people)}")
        for key, items in data['skipped'].items():
            print(f"{key:14} rows {len(items):4}  people {len({i['user_id'] for i in items})}")
        print(f'\nTO {args.direction.upper()} (user | institution | units verified there | note):')
        for i in data['items']:
            print(f"  #{i['user_id']} {i['username']} ({i['name']}) | {i['institution']} "
                  f"[{i['code']}] | {i['units']} | {i['note'] or ''}")
        if args.json:
            with open(args.json, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
            print(f'written {args.json}')
        if args.apply:
            changed = ar.apply(pairs=[(i['user_id'], i['institution_id'])
                                      for i in data['items']], **params)
            db.session.commit()
            print(f"\nAPPLIED: {args.direction} {args.right} ({args.module}) on {len(changed)} rows")
        else:
            print('\nDry run: nothing written.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
