"""Compare /identify default-queue latency: two tiers vs three tiers (read-only).

Calls next_observation_for_identification for a user with the deployed sort,
then with the three-tier rank patched in (animal votes > only 'Empty' > none),
and prints wall/SQL time per call plus EXPLAIN ANALYZE of the main query.
Works against whatever routes.py is installed: the three-tier variant is
injected by wrapping _apply_identify_sort, so it can be measured before deploy.

Usage:
    venv/bin/python -m scripts.probe_identify_tiers <username> [runs]
"""
import sys
import time

from flask_login import login_user
from sqlalchemy import case, event, func, select, text

from app import create_app
from app.models import User


def tier_expr(routes):
    from app.camera_traps.models import Identification, Photo
    return select(func.coalesce(func.max(case(
        (func.coalesce(Identification.species_id, 0) != -1, 2),
        else_=1)), 0)).where(
        Identification.photo_id == routes._series_photo_probe(Photo.id)
    ).scalar_subquery()


def main():
    username = sys.argv[1]
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    app = create_app()
    with app.app_context():
        from app.camera_traps import routes
        from app.camera_traps.database import get_ct_session
        user = User.query.filter_by(username=username).first()
        if user is None:
            sys.exit(f'no user {username}')

        engine = get_ct_session().get_bind()
        stmts = []

        @event.listens_for(engine, 'before_cursor_execute')
        def _before(conn, cursor, statement, params, context, executemany):
            context._t0 = time.perf_counter()

        @event.listens_for(engine, 'after_cursor_execute')
        def _after(conn, cursor, statement, params, context, executemany):
            stmts.append((time.perf_counter() - context._t0, statement, params))

        original = routes._apply_identify_sort

        def three_tier(query, sort_by, status_filter, extra_filters=(), voted_expr=None):
            if voted_expr is not None:
                voted_expr = tier_expr(routes)
            return original(query, sort_by, status_filter, extra_filters, voted_expr=voted_expr)

        def three_tier_nojit(query, sort_by, status_filter, extra_filters=(), voted_expr=None):
            get_ct_session().execute(text('SET LOCAL jit = off'))
            return three_tier(query, sort_by, status_filter, extra_filters, voted_expr)

        slowest = {}
        variants = (('two tiers', original), ('three tiers', three_tier),
                    ('3 tiers nojit', three_tier_nojit))
        for label, patch in variants + variants:
            routes._apply_identify_sort = patch
            times = []
            for i in range(runs):
                stmts.clear()
                url = '/uk/camera-traps/api/next-observation-for-identification'
                with app.test_request_context(url):
                    from flask import g
                    g.lang_code = 'uk'
                    login_user(user)
                    t0 = time.perf_counter()
                    resp = routes.next_observation_for_identification.__wrapped__.__wrapped__('uk')
                    total = time.perf_counter() - t0
                times.append(total)
                top = max(stmts, key=lambda s: s[0])
                slowest[label] = top
            times.sort()
            print(f'{label:12} median {times[len(times)//2]*1000:6.0f} ms  '
                  f'min {times[0]*1000:6.0f}  max {times[-1]*1000:6.0f}  '
                  f'(slowest stmt {top[0]*1000:.0f} ms)')
        routes._apply_identify_sort = original

        if '--explain' in sys.argv:
            for label, (_dt, statement, params) in slowest.items():
                print(f'\n=== EXPLAIN {label}')
                raw = engine.raw_connection()
                try:
                    cur = raw.cursor()
                    for jit in ('on', 'off'):
                        cur.execute(f'SET LOCAL jit = {jit}')
                        best = None
                        for _ in range(3):
                            cur.execute('EXPLAIN (ANALYZE) ' + statement, params)
                            lines = [l for (l,) in cur.fetchall()]
                            ms = float(lines[-1].split()[2])
                            best = ms if best is None else min(best, ms)
                        print(f'  jit={jit}: best execution {best:.0f} ms')
                    if label == 'three tiers':
                        print(cur.mogrify(statement, params).decode())
                    raw.rollback()
                finally:
                    raw.close()


if __name__ == '__main__':
    main()
