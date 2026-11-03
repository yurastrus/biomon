"""Local UI harness: species dashboard -> sample-upload deep link.

SQLite main DB and faked PAM data: touches no real database. Serves on
http://127.0.0.1:5081; open /__login first (logs in a fake admin, lands on
/uk/pam/pam_detailed?species=Bufo bufo...). The dashboard's JSON endpoints and
the sampler's /species and /prepare are replaced by fakes; /prepare echoes the
request body back as one segment so the browser log shows what was sent.

    venv/Scripts/python scripts/pam_sample_link_ui_harness.py
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()

MODELS = [
    {'model_id': 1, 'label': 'BirdNET 2.4', 'is_reference': True},
    {'model_id': 2, 'label': 'Perch v2', 'is_reference': False},
]
SPECIES = [{'value': 'Bufo bufo', 'text': 'Ропуха сіра (Bufo bufo)'}]
# Locations 1-3 belong to institution 1, 4-5 to institution 2.
LOCS = [SimpleNamespace(location_id=i, location_name=f'Локація {i}',
                        location_name_en=f'Location {i}',
                        institution_id=1 if i <= 3 else 2) for i in range(1, 6)]


def _result(rows=()):
    r = MagicMock()
    r.fetchall.return_value = list(rows)
    r.mappings.return_value.fetchall.return_value = []
    return r


def fake_execute(stmt, params=None, *a, **k):
    if 'LEFT JOIN location_institutions li ON l.location_id' in str(stmt):
        return _result(LOCS)
    return _result()


conn = MagicMock()
conn.execute.side_effect = fake_execute
for target in ('app.pam.utils.get_pam_db_connection', 'app.pam.routes.get_pam_db_connection'):
    patch(target, return_value=conn).start()
patch('app.pam.utils.get_available_species', return_value=SPECIES).start()
for target in ('app.pam.utils.get_models_list', 'app.pam.routes.get_models_list'):
    patch(target, return_value=MODELS).start()
patch('app.pam.routes.get_reference_model_id', return_value=1).start()
patch('app.pam.routes.get_earliest_recording_year', return_value=2019).start()

from flask import jsonify, request  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app('testing')
assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'), 'refusing: not SQLite'
app.config['WTF_CSRF_ENABLED'] = False


def _points():
    # Institution filter narrows the map like the real query does.
    inst = request.args.get('institution_id', '')
    ids = [l.location_id for l in LOCS if not inst or str(l.institution_id) in inst.split(',')]
    return jsonify([{'location_id': i, 'location_name': f'Локація {i}', 'lat': 49.8 + i / 50,
                     'lon': 23.6 + i / 50, 'detection_count': 10 * i, 'is_verified': False}
                    for i in ids])


def _prepare():
    body = request.get_json(silent=True) or {}
    return jsonify({'success': True, 'count': 1, 'model_id': body.get('model_id'),
                    'segments': [], 'echo': body})


FAKES = {
    '/<lang_code>/api/pam/get-filters-data': lambda lang_code: jsonify({
        'institutions': [{'id': 1, 'text': 'Проект А'}, {'id': 2, 'text': 'Проект Б'}],
        'biotopes': [], 'locations': [{'id': l.location_id, 'text': l.location_name} for l in LOCS]}),
    '/<lang_code>/api/pam/get-unique-points': lambda lang_code: _points(),
    '/<lang_code>/api/pam/sample/species': lambda lang_code: jsonify({'species': [
        {'species_id': 7, 'scientific_name': 'Bufo bufo', 'text': 'Ропуха сіра (Bufo bufo)'}]}),
    '/<lang_code>/api/pam/sample/prepare': lambda lang_code: _prepare(),
}
for rule in app.url_map.iter_rules():
    if rule.rule in FAKES:
        app.view_functions[rule.endpoint] = FAKES[rule.rule]

with app.app_context():
    from app.models import Institution, Role, User
    db.create_all()
    db.session.add(Institution(id=1, name_uk='Проект А', code='A'))
    db.session.add(Institution(id=2, name_uk='Проект Б', code='B'))
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
    return redirect('/uk/pam/pam_detailed?species=Bufo%20bufo&start_date=2025-11-03'
                    '&end_date=2026-11-03&confidence=0.93&mode=model&model_id=2')


if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', 5081)), debug=False, use_reloader=False)
