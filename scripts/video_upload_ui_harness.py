"""Local UI harness for /upload-video (2026-11-03).

SQLite main DB with a fake admin, two fake locations, and the CT database left
out entirely: batch creation, frame storage and grouping are stubbed. The new
endpoints that decide a clip's time (metadata, sign-clip) and the token check
in process-frame run for real, so what this shows is what the server would
store. Serves on http://127.0.0.1:5080; open /__login first.

Sample clips are served from VIDEO_HARNESS_SAMPLES (os.pathsep-separated; default:
the desktop folder of camera-trap videos and two Roztochya archive folders) at /__sample/<name>, so the browser can be handed real
files without a file dialog. Stored frames are listed at /__frames.

    venv/Scripts/python scripts/video_upload_ui_harness.py
"""
import os
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ARCHIVE = 'F:/20_Phototraps_photos/1 - Просортовані/'
SAMPLES = os.environ.get('VIDEO_HARNESS_SAMPLES', os.pathsep.join([
    os.path.expanduser('~/Desktop/відео з фотопасток'),
    _ARCHIVE + '@ Верещицьке, центр @ 49.96264 @ 23.64623 @/2025',
    _ARCHIVE + '@ Ставчанське к. 26, біля галявини в хвойному лісі @ 49.92390 @ 23.76928 @/2025',
])).split(os.pathsep)

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()
patch('app.camera_traps.routes.get_ct_session', return_value=MagicMock()).start()
patch('app.camera_traps.routes.close_ct_session').start()
patch('app.camera_traps.video_upload.list_profiles', return_value=[]).start()

LOCATIONS = [
    SimpleNamespace(id=1, name='Верещицьке, центр', latitude=49.96264, longitude=23.64623),
    SimpleNamespace(id=2, name='Ставчанське к. 26', latitude=49.92390, longitude=23.76928),
]
LOCATIONS_DATA = [{'id': l.id, 'name': l.name, 'institution_ids': [1]} for l in LOCATIONS]
patch('app.camera_traps.routes._accessible_locations',
      return_value=(LOCATIONS, LOCATIONS_DATA,
                    [SimpleNamespace(id=1, name_uk='Розточчя', name_en='Roztochya')])).start()
patch('app.camera_traps.background_tasks.get_storage_disk_usage',
      return_value={'free_bytes': 50 * 1024 ** 3}).start()

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app('testing')
assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'), 'refusing: not SQLite'
app.config['WTF_CSRF_ENABLED'] = False
app.config.setdefault('GEOSERVER_URL', '')

with app.app_context():
    from app.models import Role, User
    db.create_all()
    u = User(username='harness_admin', password_hash='x')
    u.roles.append(Role(name='admin'))
    db.session.add(u)
    db.session.commit()
    uid = u.id

FRAMES = []


def fake_create_batch(lang_code):
    from flask import jsonify, request
    print('CREATE-BATCH', request.get_json(), flush=True)
    return jsonify({'success': True, 'batch_id': uuid.uuid4().hex})


def fake_process_frame(lang_code):
    """The real token check, minus the photo pipeline."""
    from flask import jsonify, request
    from app.camera_traps.video_upload import VideoUploadError, frame_capture_time
    try:
        captured = frame_capture_time(
            request.form['token'], request.form['batch_id'],
            request.form.get('filename', ''), int(request.form.get('frame_index', 0)))
    except VideoUploadError as e:
        return jsonify({'error': str(e)}), 400
    f = request.files['file']
    FRAMES.append({'clip': request.form.get('filename'), 'name': f.filename,
                   'bytes': len(f.read()), 'captured_at': captured.isoformat(),
                   'save_original': request.form.get('save_original')})
    return jsonify({'success': True, 'captured_at': captured.isoformat()})


def fake_finalize(lang_code):
    from flask import jsonify
    return jsonify({'success': True}), 202


def fake_status(lang_code, batch_id):
    from flask import jsonify
    return jsonify({'status': 'completed'})


app.view_functions['camera_traps.create_batch'] = fake_create_batch
app.view_functions['camera_traps.video_process_frame'] = fake_process_frame
app.view_functions['camera_traps.finalize_batch_async'] = fake_finalize
app.view_functions['camera_traps.get_batch_status_api'] = fake_status


@app.route('/__sample/<path:name>')
def _sample(name):
    from flask import abort, send_from_directory
    for folder in SAMPLES:
        if os.path.isfile(os.path.join(folder, name)):
            return send_from_directory(folder, name)
    abort(404)


@app.route('/__samples')
def _samples():
    from flask import jsonify
    return jsonify(sorted(n for f in SAMPLES if os.path.isdir(f) for n in os.listdir(f)
                          if n.lower().endswith(('.mov', '.mp4', '.avi', '.m4v'))))


@app.route('/__frames')
def _frames():
    from flask import jsonify
    return jsonify(FRAMES)


@app.route('/__login')
def _login():
    from flask import redirect
    from flask_login import login_user
    login_user(db.session.get(User, uid))
    return redirect('/uk/camera-traps/upload-video')


if __name__ == '__main__':
    app.run(port=5080, debug=False, use_reloader=False)
