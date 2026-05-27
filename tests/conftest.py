"""
Shared pytest fixtures для biomon.

Призначення:
  Базова інфраструктура для нових pytest-тестів (CT + PAM модулі).
  Існуючі unittest.TestCase-тести не торкаємо — pytest їх запускає як є.

Як працює ізоляція БД:
  - SQLALCHEMY_DATABASE_URI → sqlite:///:memory: (через env var DATABASE_URL)
  - CT engine (`app.camera_traps.database.create_engine`) — патчиться через MagicMock,
    бо CT моделі прив'язані до власної БД (ct_db) і не повинні
    зачіпати prod під час тестів.
  - PAM_DB / GEODATA — теж мокаються при потребі (через `mock_pam_conn` фікстуру).

Запуск:
    venv/Scripts/python -m pytest -v
"""
import os
import contextlib
import pytest
from unittest.mock import MagicMock, patch


# ── env-vars мають бути встановлені ДО імпорту app ───────────────────────────
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-secret')


@pytest.fixture(scope='session')
def _ct_engine_patch():
    """Підміняємо CT-engine на MagicMock на весь тест-сесій рівень."""
    patcher = patch(
        'app.camera_traps.database.create_engine',
        return_value=MagicMock(),
    )
    patcher.start()
    yield
    patcher.stop()


@pytest.fixture(scope='session')
def app(_ct_engine_patch):
    """Flask-додаток у режимі testing з SQLite in-memory."""
    from app import create_app
    flask_app = create_app('testing')
    flask_app.config['GEOSERVER_URL'] = 'http://test-geoserver'
    flask_app.config['WTF_CSRF_ENABLED'] = False
    return flask_app


@pytest.fixture(scope='function')
def db_session(app):
    """
    Чистий рівень БД на кожен тест: create_all → yield → drop_all.
    Тести, що чіпляють тільки head/models — використовують цю фікстуру.
    """
    from app.extensions import db
    with app.app_context():
        db.create_all()
        try:
            yield db.session
        finally:
            db.session.rollback()
            db.drop_all()


@pytest.fixture(scope='function')
def client(app, db_session):
    """Flask test client з підготовленою БД."""
    return app.test_client()


# ── Factories ────────────────────────────────────────────────────────────────

@pytest.fixture
def make_role(db_session):
    """Створює (або повертає існуючу) Role з заданим іменем."""
    from app.models import Role
    created = []

    def _make(name):
        existing = db_session.query(Role).filter_by(name=name).first()
        if existing:
            return existing
        r = Role(name=name)
        db_session.add(r)
        db_session.flush()
        created.append(r)
        return r
    return _make


@pytest.fixture
def make_user(db_session, make_role):
    """Створює User з ролями. Пароль за замовчуванням = 'pass'."""
    from app.extensions import bcrypt
    from app.models import User

    def _make(username='testuser', password='pass', roles=()):
        pw = bcrypt.generate_password_hash(password).decode()
        u = User(username=username, password_hash=pw)
        for rname in roles:
            u.roles.append(make_role(rname))
        db_session.add(u)
        db_session.commit()
        return u
    return _make


@pytest.fixture
def auth_client(app, db_session, make_user):
    """
    Фабрика залогінених test-клієнтів.
    Використання: `c = auth_client(role='admin')` → клієнт з admin-сесією.
    """
    def _login(role='admin', username=None):
        username = username or f'test_{role}'
        u = make_user(username=username, roles=(role,))
        cl = app.test_client()
        with cl.session_transaction() as sess:
            sess['_user_id'] = str(u.id)
            sess['_fresh'] = True
        return cl
    return _login


@pytest.fixture
def ct_session():
    """
    SQLite in-memory session з підмножиною CT-таблиць (без ARRAY/JSONB).
    Створюємо лише таблиці, що не використовують PG-only типи —
    цього достатньо для model smoke-тестів.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.camera_traps import models as m

    engine = create_engine('sqlite:///:memory:')
    tables = [
        m.Species.__table__,
        m.Location.__table__,
        m.Biotope.__table__,
        m.Deployment.__table__,
        m.Observation.__table__,
        m.UploadBatch.__table__,
        m.Photo.__table__,
        m.BehaviorType.__table__,
        m.Identification.__table__,
        m.UserProfile.__table__,
    ]
    for t in tables:
        t.create(bind=engine, checkfirst=True)

    Session = sessionmaker(bind=engine)
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


@pytest.fixture
def make_ct_species(ct_session):
    from app.camera_traps.models import Species

    def _make(scientific_name='Vulpes vulpes', category='mammal', **kwargs):
        sp = Species(
            scientific_name=scientific_name,
            category=category,
            common_name_ua=kwargs.pop('common_name_ua', 'Лисиця'),
            common_name_en=kwargs.pop('common_name_en', 'Red Fox'),
            **kwargs,
        )
        ct_session.add(sp)
        ct_session.commit()
        return sp
    return _make


@pytest.fixture
def make_ct_location(ct_session):
    from app.camera_traps.models import Location
    from decimal import Decimal

    def _make(name='Test Location', latitude=49.5, longitude=24.5, **kwargs):
        loc = Location(name=name, latitude=Decimal(str(latitude)),
                       longitude=Decimal(str(longitude)), **kwargs)
        ct_session.add(loc)
        ct_session.commit()
        return loc
    return _make


@pytest.fixture
def make_ct_deployment(ct_session, make_ct_location):
    from app.camera_traps.models import Deployment
    from datetime import date

    def _make(location=None, name='2025_Summer_Test_1001',
              start_date=date(2025, 7, 1), end_date=date(2025, 9, 30), **kwargs):
        location = location or make_ct_location()
        dep = Deployment(
            location_id=location.id,
            name=name,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )
        ct_session.add(dep)
        ct_session.commit()
        return dep
    return _make


@pytest.fixture
def make_ct_observation(ct_session, make_ct_location):
    from app.camera_traps.models import Observation
    from datetime import datetime, timedelta

    def _make(location=None, uploaded_by_id=1, **kwargs):
        location = location or make_ct_location()
        start = kwargs.pop('series_start_time', datetime(2025, 1, 1, 12, 0))
        end = kwargs.pop('series_end_time', start + timedelta(minutes=5))
        obs = Observation(
            location_id=location.id,
            series_start_time=start,
            series_end_time=end,
            uploaded_by_id=uploaded_by_id,
            **kwargs,
        )
        ct_session.add(obs)
        ct_session.commit()
        return obs
    return _make


@pytest.fixture
def make_ct_photo(ct_session, make_ct_observation):
    from app.camera_traps.models import Photo
    from datetime import datetime
    counter = {'n': 0}

    def _make(observation=None, **kwargs):
        observation = observation or make_ct_observation()
        counter['n'] += 1
        p = Photo(
            observation_id=observation.id,
            original_filename=kwargs.pop('original_filename', f'IMG_{counter["n"]}.jpg'),
            system_filename=kwargs.pop('system_filename', f'sys_IMG_{counter["n"]}.jpg'),
            captured_at=kwargs.pop('captured_at', datetime(2025, 1, 1, 12, 0)),
            **kwargs,
        )
        ct_session.add(p)
        ct_session.commit()
        return p
    return _make


@pytest.fixture
def mock_pam_conn():
    """
    Контекст-менеджер для патчингу `get_pam_db_connection` у двох місцях.
    Будь-який .execute(...) повертає 'порожній' result.
    """
    @contextlib.contextmanager
    def _ctx():
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar.return_value = 0
        result.__iter__ = lambda self: iter([])
        conn.execute.return_value = result

        patches = [
            patch('app.pam.utils.get_pam_db_connection', return_value=conn),
        ]
        started = []
        try:
            for p in patches:
                try:
                    started.append(p.__enter__())
                except (AttributeError, ModuleNotFoundError):
                    pass
            yield conn
        finally:
            for p in patches:
                with contextlib.suppress(Exception):
                    p.__exit__(None, None, None)
    return _ctx
