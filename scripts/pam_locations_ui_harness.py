"""Local UI harness for the PAM and CT manage-locations pages.

SQLite main DB, fake PAM connection and fake CT session: touches no real
database. Serves on http://127.0.0.1:5078; open /__login first (logs in a
fake admin), then /uk/pam/manage-locations, /uk/pam/service-plan or
/uk/camera-traps/manage-locations.

    venv/Scripts/python scripts/pam_locations_ui_harness.py
"""
import os
import sys
from collections import namedtuple
from datetime import datetime, timedelta
from types import SimpleNamespace
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


DEVICES = ['SM Micro 2', 'SM Micro', 'SM Mini', 'SM Mini 2', 'AudioMoth', None]
NOW = datetime.now()


def _plan_location(i):
    return SimpleNamespace(location_id=i, location_name=f'Локація {i}', location_name_en=f'Location {i}',
                           lat=49.8 + (i % 6) / 30, lon=23.6 + (i // 6) / 15, inst_ids=[1])


def _plan_install(i):
    """One installation per location, spread over time so statuses vary."""
    when = NOW - timedelta(days=3 + i * 4)
    return SimpleNamespace(location_id=i, visit_datetime=when, visit_purpose_id=2,
                           recording_hours_per_day=6, battery_type_id=1 + i % 2,
                           estimated_recording_hours=260 if i % 2 == 0 else 340,
                           capacity_gb=59.6 if i % 3 else 119.2, is_operational=True,
                           last_recording=when + timedelta(days=min(i * 3, 40)),
                           recorded_hours=0.0, sd_card_status_id=3,
                           battery_name='Акумулятори NiMH 2500 mAh' if i % 2 else 'Літієві батарейки AA',
                           device_type_id=None if DEVICES[i % 6] is None else i % 6 + 1,
                           device_name=DEVICES[i % 6])


def fake_execute(stmt, params=None, *a, **k):
    sql = str(stmt)
    # Service plan page.
    if 'ARRAY(SELECT li2.institution_id' in sql:
        return _result([_plan_location(i) for i in range(1, 25)])
    if 'AS last_data_date' in sql:
        return _result([SimpleNamespace(location_id=i, last_data_date=_plan_install(i).last_recording)
                        for i in range(1, 25)])
    if 'LEAD(sv.visit_datetime)' in sql or 'AS device_name' in sql:
        rows = [_plan_install(i) for i in range(1, 25)]
        removed = SimpleNamespace(**{**vars(_plan_install(22)), 'visit_purpose_id': 3,
                                     'visit_datetime': NOW - timedelta(days=2),
                                     'battery_type_id': None, 'capacity_gb': None,
                                     'device_type_id': None})
        return _result(rows + [removed])
    if 'FROM device_types' in sql:
        return _result([Ref(i + 1, d or 'Інший', d or 'Other') for i, d in enumerate(DEVICES)])
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
    app.run(port=int(os.environ.get('PORT', 5078)), debug=False, use_reloader=False)
