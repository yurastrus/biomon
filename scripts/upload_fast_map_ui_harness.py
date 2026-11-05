"""Local UI harness for the /upload-fast map (2026-11-05).

Renders the real upload_fast.html with fake locations, no CT database: a stack
of four pins a few metres apart, a pair, and some singles, with photo counts
spread out so the count slider has something to filter. Checks the fan-out of
stacked pins and the two-thumb count slider.

    venv/Scripts/python scripts/upload_fast_map_ui_harness.py
    open http://127.0.0.1:5081/__login
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()

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


def _loc(i, name, lat, lon, n, d0='2026-03-01', d1='2026-09-01'):
    return {'id': i, 'name': name, 'latitude': lat, 'longitude': lon, 'institution_ids': [1],
            'photo_count': n, 'date_start': d0 if n else None, 'date_end': d1 if n else None}


LOCATIONS = [
    # stack of four within ~5 m
    _loc(1, 'Стек А1', 49.93000, 23.70000, 120),
    _loc(2, 'Стек А2', 49.93002, 23.70003, 4500),
    _loc(3, 'Стек А3', 49.92998, 23.70004, 900),
    _loc(4, 'Стек А4', 49.93001, 23.69997, 15),
    # pair ~10 m apart
    _loc(5, 'Пара Б1', 49.95000, 23.65000, 300),
    _loc(6, 'Пара Б2', 49.95008, 23.65004, 2000),
    _loc(7, 'Окрема В', 49.90000, 23.75000, 50),
    _loc(8, 'Окрема Г', 49.96000, 23.76000, 0),
]


def fake_upload_fast(lang_code):
    from flask import render_template
    from app.camera_traps.forms import UploadForm
    form = UploadForm()
    form.location.choices = [(-1, '--')] + [(l['id'], l['name']) for l in LOCATIONS] + [(0, '***')]
    return render_template(
        'upload_fast.html', form=form, locations_data=LOCATIONS,
        max_photo_count=max(l['photo_count'] for l in LOCATIONS), geoserver_url='',
        institutions=[SimpleNamespace(id=1, name_uk='Розточчя', name_en='Roztochya')],
        upload_allowed=True, free_mb=50000, min_free_mb=1024,
        keep_originals_allowed=True, min_originals_free_gb=20)


app.view_functions['camera_traps.upload_fast'] = fake_upload_fast


@app.route('/__login')
def _login():
    from flask import redirect
    from flask_login import login_user
    login_user(db.session.get(User, uid))
    return redirect('/uk/camera-traps/upload-fast')


if __name__ == '__main__':
    app.run(port=5081, debug=False, use_reloader=False)
