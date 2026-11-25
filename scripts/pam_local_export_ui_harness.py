"""Local UI harness: sample-upload page, server mode and local-folder export.

SQLite main DB and faked PAM data: touches no real database. Serves on
http://127.0.0.1:5083; open /__login first (logs in a fake admin, lands on
/uk/pam/verification/sample-upload).

The sampler's /species, /prepare and /recording-locations are fakes. /prepare
answers like the real one: a server-mode request gets a plain plan, a
``destination: local`` request gets a plan per species with vote fields, and
both carry ``echo`` (the request body) so the browser can check what was sent.
Recording names are REC<location>_2025030<n>_120000.wav at locations 1-3.

The native folder picker cannot be driven by automation: in the browser,
replace ``window.showDirectoryPicker`` with a function returning directories of
the origin-private file system (navigator.storage.getDirectory()), which have
the same API.

    venv/Scripts/python scripts/pam_local_export_ui_harness.py
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
LOCS = [SimpleNamespace(location_id=i, location_name=f'Локація {i}',
                        location_name_en=f'Location {i}',
                        institution_id=1 if i <= 2 else 2) for i in range(1, 4)]
SPECIES = [
    {'species_id': 5, 'scientific_name': 'Bufo bufo', 'text': 'Ропуха сіра (Bufo bufo)',
     'class_name': 'Amphibia'},
    {'species_id': 6, 'scientific_name': 'Rana temporaria', 'text': 'Жаба трав’яна (Rana temporaria)',
     'class_name': 'Amphibia'},
    {'species_id': 7, 'scientific_name': 'Strix aluco', 'text': 'Сова сіра (Strix aluco)',
     'class_name': 'Aves'},
]


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


def _segment(species, n, location_id, detection_id, state='confirmed'):
    rec = f'REC{location_id}_2025030{n}_120000.wav'
    return {
        'detection_id': detection_id, 'recording_id': 100 + n * 10 + location_id,
        'species_id': next(s['species_id'] for s in SPECIES if s['scientific_name'] == species),
        'species_name': species, 'model_id': 1, 'recording_filename': rec,
        'location_id': location_id,
        'segment_filename': f'0.9{n}0_REC{location_id}_2025030{n}_120000_sec{n}_part1.flac',
        'location_name': f'REC{location_id}', 'start_s': float(n), 'end_s': float(n + 3),
        'confidence': 0.9 + n / 100, 'recorded_date': f'2025-03-0{n}',
        'recorded_time': '12:00:00',
    } | ({'consensus': 1 if state == 'confirmed' else None, 'votes': 2,
          'positive_votes': 2, 'verification_state': state} if state else {})


def _prepare():
    body = request.get_json(silent=True) or {}
    if body.get('destination') == 'local':
        names = body.get('species_names') or [body.get('species_name')]
        segments = []
        for k, name in enumerate(names):
            # Recording 1 of location 1 is shared by every species; the last
            # one (n=4) has no file in the archive, so it shows as missing.
            for n in (1, 2, 3, 4):
                segments.append(_segment(name, n, 1 + (n + k) % 3 if n > 1 else 1, 1000 * k + n))
        return jsonify({'success': True, 'count': len(segments), 'model_id': 1,
                        'segments': segments, 'destination': 'local',
                        'unit': body.get('unit'), 'echo': body,
                        'per_species': [{'species_name': n, 'count': 4} for n in names]})
    seg = _segment(body.get('species_name'), 1, 1, 1, state=None)
    return jsonify({'success': True, 'count': 1, 'model_id': body.get('model_id'),
                    'segments': [seg], 'echo': body})


def _recording_locations():
    names = (request.get_json(silent=True) or {}).get('filenames') or []
    out = {}
    for name in names:
        if name.startswith('REC') and name[3:4].isdigit():
            out[name] = {'location_id': int(name[3]), 'lat': None, 'lon': None}
    return jsonify({'locations': out})


FAKES = {
    '/<lang_code>/api/pam/sample/species': lambda lang_code: jsonify({'species': SPECIES}),
    '/<lang_code>/api/pam/sample/prepare': lambda lang_code: _prepare(),
    '/<lang_code>/api/pam/sample/recording-locations': lambda lang_code: _recording_locations(),
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
    u.roles.append(Role(name='pam_verifier'))
    db.session.add(u)
    db.session.commit()
    uid = u.id


@app.route('/__login')
def _login():
    from flask import redirect
    from flask_login import login_user
    login_user(db.session.get(User, uid))
    return redirect('/uk/pam/verification/sample-upload')


if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', 5083)), debug=False, use_reloader=False)
