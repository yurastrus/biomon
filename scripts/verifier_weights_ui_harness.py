"""Local UI harness for /admin/verifier-weights (2026-11-23).

SQLite main DB with a fake admin and a few named people; an in-memory SQLite
CT database with species and a verifier_species_weights table filled with
plausible records (a heavy expert, mid verifiers, newcomers). Nothing touches
a real database. Serves on http://127.0.0.1:5081; open /__login first.

    venv/Scripts/python scripts/verifier_weights_ui_harness.py
"""
import os
import random
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()

ct_engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                          poolclass=StaticPool)
CTSession = sessionmaker(bind=ct_engine)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app('testing')
assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'), 'refusing: not SQLite'
app.config['WTF_CSRF_ENABLED'] = False

from app.camera_traps import models as m  # noqa: E402

m.Species.__table__.create(bind=ct_engine)
m.VerifierSpeciesWeight.__table__.create(bind=ct_engine)

SPECIES = [(-1, 'Пусто'), (-5, 'Людина'), (-6, 'Неможливо визначити'), (1, 'Козуля'),
           (2, 'Олень благородний'), (3, 'Кабан'), (4, 'Лисиця звичайна'), (5, 'Рись'),
           (6, 'Вовк'), (7, 'Куниця лісова'), (8, 'Борсук європейський'), (9, 'Зубр')]
PEOPLE = [('yura', 'Юрій Струс', 40000), ('olena', 'Олена Коваль', 9000),
          ('taras', 'Тарас Мельник', 2500), ('iryna', 'Ірина Бондар', 600),
          ('newbie', 'Новий Волонтер', 12), ('andrii', 'Андрій Ткач', 1400)]

with app.app_context():
    from app.models import Role, User
    db.create_all()
    admin = User(username='harness_admin', password_hash='x')
    admin.roles.append(Role(name='admin'))
    db.session.add(admin)
    uids = []
    for login, full, _n in PEOPLE:
        first, last = full.split()
        u = User(username=login, password_hash='x', first_name=first, last_name=last)
        db.session.add(u)
        db.session.flush()
        uids.append(u.id)
    db.session.commit()
    admin_id = admin.id

rng = random.Random(3)
with ct_engine.begin() as conn:
    for sid, name in SPECIES:
        conn.exec_driver_sql(
            'INSERT INTO species (id, scientific_name, common_name_ua, category, is_active) '
            'VALUES (?, ?, ?, ?, 1)', (sid, f'sp{sid}', name, 'mammal'))
    for uid, (_l, _f, total) in zip(uids, PEOPLE):
        for sid, _name in SPECIES:
            share = 0.8 if sid == -1 else rng.random() * 0.06
            n = int(total * share)
            if n == 0:
                continue
            acc = 0.99 if total > 5000 else 0.93 if total > 1000 else 0.8
            if sid in (7, 5):
                acc -= 0.15
            confirmed = min(n, int(round(n * min(0.999, acc + rng.uniform(-0.03, 0.02)))))
            conn.exec_driver_sql(
                'INSERT INTO verifier_species_weights (user_id, species_id, n, confirmed, updated_at) '
                'VALUES (?, ?, ?, ?, ?)', (uid, sid, n, confirmed, datetime(2026, 11, 23, 12, 16)))

patch('app.camera_traps.weights_routes.get_ct_session', side_effect=lambda: CTSession()).start()
patch('app.camera_traps.weights_routes.close_ct_session').start()


@app.route('/__login')
def _login():
    from flask import redirect
    from flask_login import login_user
    login_user(db.session.get(User, admin_id))
    return redirect('/uk/camera-traps/admin/verifier-weights')


if __name__ == '__main__':
    app.run(port=5081, debug=False, use_reloader=False)
