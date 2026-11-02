"""Local UI harness for /identify keyboard shortcuts (2026-11-02).

SQLite main DB, SQLite CT session with a few species and behaviour tags (some
with fixed keys), a fake "next series" API that serves numbered SVG frames.
Touches no real database. Serves on http://127.0.0.1:5079; open /__login
first (logs in a fake admin and lands on /uk/camera-traps/identify).

    venv/Scripts/python scripts/identify_hotkeys_ui_harness.py
"""
import itertools
import os
import sys
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.camera_traps import models as m  # noqa: E402

ct_engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                          poolclass=StaticPool)
for t in (m.Species.__table__, m.BehaviorType.__table__):
    t.create(bind=ct_engine)
ct_session = sessionmaker(bind=ct_engine)()
ct_session.add_all([
    m.Species(id=-1, scientific_name='empty', category='empty',
              common_name_ua='Пусто', common_name_en='Empty'),
    m.Species(id=1, scientific_name='Vulpes vulpes', category='mammal',
              common_name_ua='Лисиця', common_name_en='Red fox'),
    m.Species(id=2, scientific_name='Capreolus capreolus', category='mammal',
              common_name_ua='Козуля', common_name_en='Roe deer'),
    m.Species(id=3, scientific_name='Sus scrofa', category='mammal',
              common_name_ua='Кабан', common_name_en='Wild boar'),
])
TAGS = [('Переміщення', 'Moving', 1), ('Самець', 'Male', 2), ('Харчування', 'Feeding', 3),
        ('Обслуговування фотопасток', 'Photo traps maintenance', 4), ('Самиця', 'Female', 5),
        ('Пильність', 'Vigilance', 6), ('Дорослий з потомством', 'Adult with juveniles', 7),
        ('Вигул собак(и)', 'Dog walking', 8), ('Молода особина', 'Juvenile', 9),
        ('Відпочинок', 'Resting', 10), ('Соціальна взаємодія', 'Social interaction', None),
        ('Їзда на велосипеді', 'Biking', None), ('Догляд за собою', 'Grooming', None),
        ('Маркування території', 'Territory marking', None)]
ct_session.add_all([m.BehaviorType(name_ua=u, name_en=e, hotkey=k) for u, e, k in TAGS])
ct_session.commit()
ct_session.close = lambda: None

patch('app.camera_traps.routes.get_ct_session', return_value=ct_session).start()
patch('app.camera_traps.routes.close_ct_session').start()
patch('app.camera_traps.routes.get_species_ranking', return_value={}).start()
patch('app.camera_traps.routes.get_behavior_ranking', return_value={}).start()
# AI filter list: "Empty" sits mid-alphabet in the real query, the template
# moves it up next to "not empty".
AI_SPECIES = [{'id': i, 'text': f'{n} ({c})'} for i, n, c in [
    (5, 'Борсук (Meles meles)', 12), (6, 'Вовк (Canis lupus)', 3), (1, 'Лисиця (Vulpes vulpes)', 40),
    (2, 'Козуля (Capreolus capreolus)', 210), (7, 'Куниця (Martes sp.)', 9), (8, 'Олень (Cervus elaphus)', 88),
    (-1, 'Пусто', 530), (3, 'Кабан (Sus scrofa)', 140), (9, 'Рись (Lynx lynx)', 2), (10, 'Заєць (Lepus europaeus)', 31),
]]
patch('app.camera_traps.ai_runner.is_ai_available', return_value=True).start()
patch('app.camera_traps.ai_runner.get_species_with_ai_predictions', return_value=AI_SPECIES).start()

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app('testing')
assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'), 'refusing: not SQLite'
app.config['WTF_CSRF_ENABLED'] = False

with app.app_context():
    from app.models import Role, User
    db.create_all()
    u = User(username='harness_admin', password_hash='x')
    u.roles.append(Role(name='admin'))
    db.session.add(u)
    from app.models import Institution
    for i, (uk, eco) in enumerate([('Розточчя', 'Розточчя'), ('Яворівський НПП', 'Розточчя'),
                                   ('Карпатський БЗ', 'Карпати'), ('Синевир', 'Карпати'),
                                   ('Ужанський НПП', 'Карпати'), ('Гуцульщина', 'Карпати'),
                                   ('Шацький НПП', 'Полісся'), ('Древлянський', 'Полісся'),
                                   ('Черемський', 'Полісся'), ('Деснянсько-Старогутський', 'Полісся')], 1):
        db.session.add(Institution(name_uk=uk, code=f'I{i}', ecoregion_uk=eco))
    db.session.commit()
    uid = u.id

_series = itertools.count(101)
SUBMITTED = []


def fake_next(lang_code):
    from flask import jsonify
    obs = next(_series)
    return jsonify({
        'observation_id': obs, 'location_name': 'Харнес', 'review_mode': False,
        'photos': [{'thumbnail_url': f'/__img/{obs}/{i}', 'captured_at': f'2026-11-02 12:00:{i:02d}'}
                   for i in range(6)],
        'ai_prediction': None, 'my_identification': None,
    })


def fake_submit(lang_code):
    from flask import jsonify, request
    SUBMITTED.append(request.get_json())
    print('SUBMIT', request.get_json(), flush=True)
    return jsonify({'ok': True}), 201


app.view_functions['camera_traps.next_observation_for_identification'] = fake_next
app.view_functions['camera_traps.submit_identification'] = fake_submit


@app.route('/__img/<int:obs>/<int:i>')
def _img(obs, i):
    from flask import Response
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420">'
           f'<rect width="100%" height="100%" fill="hsl({i * 50},40%,45%)"/>'
           f'<text x="50%" y="55%" font-size="90" text-anchor="middle" fill="#fff">'
           f'{obs} · {i + 1}</text></svg>')
    return Response(svg, mimetype='image/svg+xml')


@app.route('/__submitted')
def _submitted():
    from flask import jsonify
    return jsonify(SUBMITTED)


@app.route('/__login')
def _login():
    from flask import redirect
    from flask_login import login_user
    login_user(db.session.get(User, uid))
    return redirect('/uk/camera-traps/identify')


if __name__ == '__main__':
    app.run(port=5079, debug=False, use_reloader=False)
