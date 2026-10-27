# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only audit: what every manager may manage, before vs after the
per-institution manager split (2026-10-27).

Before the split the global ``manager`` role made a person manager of every
institution they could *see* in a module. After it, they manage only the
institutions flagged ``can_manage_ct`` / ``can_manage_pam`` in
``user_institutions``. The migration (c3d9e1f27a40) seeds those flags from the
view flags of every current manager, so nobody should lose anything; this script
checks that on the real database.

For each holder of the ``manager`` role, per module:
  * OLD: institutions with a view flag (what the role used to cover), and the
    CT / PAM locations linked to them;
  * NEW: ``User.manage_institution_ids`` from the running code, and the locations
    linked to those institutions.

Where NEW comes from:
  * production already migrated (columns exist): the real rows, read through the
    real ORM code;
  * not migrated yet: a **simulation**. The users, roles and access rows are
    copied into an in-memory SQLite database, the migration's own seeding
    statement (``seed_sql``) is run there, and the real ORM code is asked.
    Production is only read.

Usage:
    venv/Scripts/python -m scripts.audit_manager_scope
    venv/Scripts/python -m scripts.audit_manager_scope --json logs/out.json

Writes nothing to any production database. Exit code 1 if anyone lost anything.
"""
import argparse
import importlib.util
import json
import os
import sys
import warnings

warnings.filterwarnings('ignore')

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

MIGRATION = os.path.join(os.path.dirname(__file__), '..', 'migrations', 'versions',
                         'c3d9e1f27a40_per_institution_manager.py')
COPIED_TABLES = ('role', 'user', 'user_roles', 'institutions', 'user_institutions')


def _seed_sql():
    spec = importlib.util.spec_from_file_location('mig_c3d9', MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.seed_sql()


def _location_ids(engine, inst_ids):
    if not inst_ids or engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT location_id FROM location_institutions "
            "WHERE institution_id = ANY(:ids)"), {'ids': list(inst_ids)}).fetchall()
    return sorted(r[0] for r in rows)


def _old_scope(main_engine):
    """{user_id: {'username', 'ct': [...], 'pam': [...]}} from the view flags."""
    with main_engine.connect() as conn:
        managers = conn.execute(text("""
            SELECT u.id, u.username FROM "user" u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN role r ON r.id = ur.role_id
            WHERE r.name = 'manager' ORDER BY u.id""")).fetchall()
        links = conn.execute(text(
            "SELECT user_id, institution_id, can_view_ct, can_view_pam "
            "FROM user_institutions")).fetchall()
        roles = conn.execute(text("""
            SELECT ur.user_id, r.name FROM user_roles ur JOIN role r ON r.id = ur.role_id
        """)).fetchall()
    out = {uid: {'username': name, 'ct': [], 'pam': [], 'roles': []}
           for uid, name in managers}
    for uid, rname in roles:
        if uid in out:
            out[uid]['roles'].append(rname)
    for uid, iid, vct, vpam in links:
        if uid in out:
            if vct:
                out[uid]['ct'].append(iid)
            if vpam:
                out[uid]['pam'].append(iid)
    return out


def _new_scope_from_app(app, user_ids):
    from app.models import User
    with app.app_context():
        return {uid: {m: sorted(User.query.get(uid).manage_institution_ids(m))
                      for m in ('ct', 'pam')} for uid in user_ids}


def _simulate(main_engine, user_ids):
    """Copy the access tables into SQLite, run the migration's seed, ask the ORM.

    Uses its own SQLite engine and a plain ORM session on it, never the app's
    ``db``: ``create_app('testing')`` keeps the .env database URL, so going
    through the app would write to production (it was tried once, 2026-10-27,
    and failed harmlessly on the first insert).
    """
    from sqlalchemy.orm import Session
    from app.models import User

    sim_engine = create_engine('sqlite:///:memory:')
    assert sim_engine.dialect.name == 'sqlite'
    tables = [db.metadata.tables[t] for t in COPIED_TABLES]
    db.metadata.create_all(sim_engine, tables=tables)

    prod_meta = inspect(main_engine)
    with main_engine.connect() as src, sim_engine.begin() as dst:
        for target in tables:
            prod_cols = {c['name'] for c in prod_meta.get_columns(target.name)}
            cols = [c.name for c in target.columns if c.name in prod_cols]
            quoted = ', '.join(f'"{c}"' for c in cols)
            rows = src.execute(text(f'SELECT {quoted} FROM "{target.name}"')).mappings().all()
            if rows:
                dst.execute(target.insert(), [dict(r) for r in rows])
        dst.execute(text(_seed_sql()))

    with Session(sim_engine) as session:
        return {uid: {m: sorted(session.get(User, uid).manage_institution_ids(m))
                      for m in ('ct', 'pam')} for uid in user_ids}


def collect(app):
    main_engine = db.engine
    ct_engine = create_engine(app.config['CT_DATABASE_URI'])
    pam_engine = (create_engine(app.config['PAM_DATABASE_URI'])
                  if app.config.get('PAM_DATABASE_URI') else None)
    migrated = 'can_manage_ct' in {
        c['name'] for c in inspect(main_engine).get_columns('user_institutions')}

    old = _old_scope(main_engine)
    new = (_new_scope_from_app(app, list(old)) if migrated
           else _simulate(main_engine, list(old)))

    report = []
    for uid, o in old.items():
        entry = {'id': uid, 'username': o['username'], 'roles': sorted(o['roles']),
                 'modules': {}}
        for module, engine in (('ct', ct_engine), ('pam', pam_engine)):
            old_ids, new_ids = sorted(o[module]), new[uid][module]
            old_locs = _location_ids(engine, old_ids)
            new_locs = _location_ids(engine, new_ids)
            entry['modules'][module] = {
                'old_institutions': old_ids, 'new_institutions': new_ids,
                'old_locations': len(old_locs), 'new_locations': len(new_locs),
                'lost_institutions': sorted(set(old_ids) - set(new_ids)),
                'lost_locations': sorted(set(old_locs) - set(new_locs)),
                'gained_institutions': sorted(set(new_ids) - set(old_ids)),
            }
        report.append(entry)
    return {'source': 'production rows' if migrated else 'simulation of the migration',
            'managers': report}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', help='write the full report to this file')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        data = collect(app)

    lost_any = False
    print(f"NEW scope from: {data['source']}; managers: {len(data['managers'])}")
    for m in data['managers']:
        parts = []
        for module, mod in m['modules'].items():
            s = (f"{module}: inst {len(mod['old_institutions'])}->{len(mod['new_institutions'])}"
                 f" loc {mod['old_locations']}->{mod['new_locations']}")
            if mod['lost_institutions'] or mod['lost_locations']:
                lost_any = True
                s += f" LOST inst={mod['lost_institutions']} loc={mod['lost_locations']}"
            if mod['gained_institutions']:
                s += f" GAINED inst={mod['gained_institutions']}"
            parts.append(s)
        print(f"#{m['id']} {m['username']} | " + ' | '.join(parts))

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print(f'written {args.json}')
    print('RESULT:', 'SOME MANAGER LOST SCOPE' if lost_any else 'no manager lost anything')
    return 1 if lost_any else 0


if __name__ == '__main__':
    sys.exit(main())
