"""Time the /identify "next series" request on the real database (read-only).

Calls next_observation_for_identification directly for a given user and
prints wall time per call, plus the SQL time of each statement. Nothing is
written: the route is a GET and only reads ct_db.

Usage:
    venv/Scripts/python -m scripts.probe_identify_latency <username> [runs]
"""
import sys
import time

from flask_login import login_user
from sqlalchemy import event

from app import create_app
from app.models import User


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
            stmts.append((time.perf_counter() - context._t0,
                          ' '.join(statement.split())[:110]))

        for sort_by in ('priority_random', 'date_desc'):
            for i in range(runs):
                stmts.clear()
                url = f'/uk/camera-traps/api/next-observation-for-identification?sort_by={sort_by}'
                with app.test_request_context(url):
                    from flask import g
                    g.lang_code = 'uk'
                    login_user(user)
                    t0 = time.perf_counter()
                    resp = routes.next_observation_for_identification.__wrapped__.__wrapped__('uk')
                    total = time.perf_counter() - t0
                status = resp[1] if isinstance(resp, tuple) else 200
                sql = sum(s[0] for s in stmts)
                print(f'{sort_by:16} run {i}: total {total*1000:7.0f} ms, '
                      f'SQL {sql*1000:7.0f} ms in {len(stmts)} stmts, status {status}')
                if i == 0:
                    for dt, s in sorted(stmts, reverse=True)[:5]:
                        print(f'      {dt*1000:7.0f} ms  {s}')


if __name__ == '__main__':
    main()
