"""Local UI harness for the PAM and CT manage-locations pages.

SQLite main DB, fake PAM connection and fake CT session: touches no real
database. Serves on http://127.0.0.1:5078; open /__login first (logs in a
fake admin), then /uk/pam/manage-locations or /uk/camera-traps/manage-locations.

    venv/Scripts/python scripts/pam_locations_ui_harness.py
"""
import os
import sys
from collections import namedtuple
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()

LocRow = namedtuple('LocRow', 'location_id location_name location_name_en lat lon institution_id')
Detail = namedtuple('Detail', 'location_id location_name location_name_en lat lon state_province')
class Ref(namedtuple('Ref', 'id name_ua name_en')):
    @property
    def _mapping(self):
        return self._asdict()


def _result(rows=(), one=None):
    r = MagicMock()
    r.fetchall.return_value = list(rows)
    r.fetchone.return_value = one
    r.mappings.return_value.fetchall.return_value = []
    r.__iter__ = lambda self: iter(list(rows))
    return r


def fake_execute(stmt, params=None, *a, **k):
    sql = str(stmt)
    if 'LEFT JOIN location_institutions li ON l.location_id' in sql:
        return _result([LocRow(i, f'Локація {i}', f'Location {i}', 49.8 + i / 100, 24.0 + i / 100, 1)
                        for i in range(1, 25)])
    if 'FROM locations WHERE location_id = :id' in sql and 'state_province' in sql:
        i = params['id']
        return _result(one=Detail(i, f'Локація {i}', f'Location {i}', 49.8, 24.0, ''))
    if 'FROM biotopes' in sql or 'FROM battery_types' in sql or 'FROM visit_purposes' in sql:
        return _result([Ref(1, 'Ліс', 'Forest')])
    if 'FROM location_institutions' in sql:
        return _result([(1,)], one=(1,))
    return _result()


conn = MagicMock()
conn.execute.side_effect = fake_execute
for target in ('app.pam.utils.get_pam_db_connection', 'app.pam.routes.get_pam_db_connection'):
    patch(target, return_value=conn).start()


# CT: /camera-traps/manage-locations with a fake ORM session.
def _ct_location(i):
    loc = MagicMock()
    loc.id, loc.name, loc.description = i, f'Локація CT {i}', ''
    loc.latitude, loc.longitude = 49.8 + i / 100, 24.0 + i / 100
    loc.biotopes, loc.stats, loc.is_valid, loc.invalid_note = [], None, True, None
    return loc


CT_LOCATIONS = {i: _ct_location(i) for i in range(1, 25)}


def fake_ct_query(model, *rest):
    from app.camera_traps.models import Location
    q = MagicMock()
    rows = list(CT_LOCATIONS.values()) if model is Location else []
    q.order_by.return_value.all.return_value = rows
    q.join.return_value.filter.return_value.order_by.return_value.distinct.return_value.all.return_value = rows
    q.get.side_effect = lambda i: CT_LOCATIONS.get(i)
    q.filter.return_value.count.return_value = 1
    return q


ct_session = MagicMock()
ct_session.query.side_effect = fake_ct_query
ct_session.execute.return_value = _result()
patch('app.camera_traps.routes.get_ct_session', return_value=ct_session).start()
patch('app.camera_traps.routes.close_ct_session').start()

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app('testing')
assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'), 'refusing: not SQLite'
app.config['WTF_CSRF_ENABLED'] = False
app.config['GEOSERVER_URL'] = 'http://harness-geoserver.invalid'

with app.app_context():
    from app.models import Institution, Role, User
    db.create_all()
    db.session.add(Institution(id=1, name_uk='Проект azazellj', code='AZ'))
    u = User(username='harness_admin', password_hash='x')
    u.roles.append(Role(name='admin'))
    db.session.add(u)
    db.session.commit()
    uid = u.id


@app.route('/__login')
def _login():
    from flask import redirect
    from flask_login import login_user
    login_user(db.session.get(User, uid))
    return redirect('/uk/pam/manage-locations')


if __name__ == '__main__':
    # One process with the patches above: no reloader.
    app.run(port=5078, debug=False, use_reloader=False)
