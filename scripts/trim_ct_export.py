# SPDX-License-Identifier: AGPL-3.0-only
"""Take camera-trap export rights away from people who do not verify there.

Owner's rule (2026-10-27): a person registered more than MIN_AGE_DAYS ago keeps
the camera-trap export right (``user_institutions.can_export_ct``) for an
institution only if they have verified at least MIN_SERIES series on that
institution's locations. Otherwise the flag is switched off **for that
institution only**. Viewing access, PAM rights and other institutions stay.

"Verified a series" = at least one identification by the person on a photo of
that series (``identifications`` -> ``photos`` -> ``observations``), counted per
institution through ``location_institutions``. Archived series count: archival
deletes files, not identification rows.

Not touched: admins (unrestricted anyway), accounts without ``created_at``
(age unknown, reported separately), accounts younger than MIN_AGE_DAYS, and the
usernames passed with --exclude (the owner excluded ``dbu`` on 2026-10-27).

Usage:
    venv/Scripts/python -m scripts.trim_ct_export              # dry run, prints the list
    venv/Scripts/python -m scripts.trim_ct_export --apply      # switches the flags off
    ... --json logs/out.json                                   # full report

The dry run writes nothing. --apply changes only can_export_ct, in one
transaction, and prints every row it changed so it can be reverted by hand.
"""
import argparse
import json
import sys
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

from sqlalchemy import create_engine, text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

MIN_AGE_DAYS = 183          # "more than 6 months"
MIN_SERIES = 50


def series_per_user_institution(ct_engine):
    """{(user_id, institution_id): distinct series identified}."""
    with ct_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT i.user_id, li.institution_id, COUNT(DISTINCT p.observation_id)
            FROM identifications i
            JOIN photos p ON p.id = i.photo_id
            JOIN observations o ON o.id = p.observation_id
            JOIN location_institutions li ON li.location_id = o.location_id
            GROUP BY i.user_id, li.institution_id
        """)).fetchall()
    return {(u, inst): n for u, inst, n in rows}


def collect(app, now, exclude=()):
    ct_engine = create_engine(app.config['CT_DATABASE_URI'])
    counts = series_per_user_institution(ct_engine)
    cutoff = now - timedelta(days=MIN_AGE_DAYS)
    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT u.id, u.username, u.first_name, u.last_name, u.created_at,
                   ui.institution_id, i.name_uk, i.code,
                   EXISTS (SELECT 1 FROM user_roles ur JOIN role r ON r.id = ur.role_id
                           WHERE ur.user_id = u.id AND r.name = 'admin') AS is_admin
            FROM user_institutions ui
            JOIN "user" u ON u.id = ui.user_id
            JOIN institutions i ON i.id = ui.institution_id
            WHERE ui.can_export_ct
            ORDER BY u.username, i.name_uk
        """)).mappings().all()

    revoke, keep, too_young, no_date, admins, excluded = [], [], [], [], [], []
    for r in rows:
        n = counts.get((r['id'], r['institution_id']), 0)
        item = {'user_id': r['id'], 'username': r['username'],
                'name': ' '.join(x for x in (r['first_name'], r['last_name']) if x),
                'created_at': r['created_at'].isoformat() if r['created_at'] else None,
                'institution_id': r['institution_id'], 'institution': r['name_uk'],
                'code': r['code'], 'series': n}
        if r['username'] in exclude:
            excluded.append(item)
        elif r['is_admin']:
            admins.append(item)
        elif r['created_at'] is None:
            no_date.append(item)
        elif r['created_at'] > cutoff:
            too_young.append(item)
        elif n < MIN_SERIES:
            revoke.append(item)
        else:
            keep.append(item)
    return {'cutoff': cutoff.isoformat(), 'min_series': MIN_SERIES,
            'revoke': revoke, 'keep': keep, 'too_young': too_young,
            'no_created_at': no_date, 'admins': admins, 'excluded': excluded}


def apply(revoke):
    changed = 0
    with db.engine.begin() as conn:
        for item in revoke:
            res = conn.execute(text("""
                UPDATE user_institutions SET can_export_ct = FALSE
                WHERE user_id = :u AND institution_id = :i AND can_export_ct
            """), {'u': item['user_id'], 'i': item['institution_id']})
            changed += res.rowcount
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--json')
    ap.add_argument('--exclude', action='append', default=[],
                    help='username to leave untouched (repeatable)')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        data = collect(app, datetime.utcnow(), exclude=set(args.exclude))
        print(f"cutoff (registered before): {data['cutoff'][:10]}, min series: {MIN_SERIES}")
        for key in ('revoke', 'keep', 'too_young', 'no_created_at', 'admins', 'excluded'):
            people = {i['user_id'] for i in data[key]}
            print(f"{key:14} rows {len(data[key]):4}  people {len(people)}")
        print('\nTO REVOKE (user | institution | series identified there):')
        for i in data['revoke']:
            print(f"  #{i['user_id']} {i['username']} ({i['name']}) | {i['institution']} "
                  f"[{i['code']}] | {i['series']}")
        if args.json:
            with open(args.json, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            print(f'written {args.json}')
        if args.apply:
            print(f"\nAPPLIED: can_export_ct switched off on {apply(data['revoke'])} rows")
        else:
            print('\nDry run: nothing written.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
