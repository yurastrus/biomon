"""
Тести для cleanup_old_photos після оптимізації (2026-05-25):
семантика збережена — лише швидкість + надійність.

Покриває:
  1. is_favorite=TRUE — фото НІКОЛИ не видаляється
  2. species_id=-2 ("Інше") — серія НІКОЛИ не архівується
  3. observation.status='archived' лише коли всі фото archived/favorite
  4. Файли видаляються ПІСЛЯ commit-у (status='archived' + файлу немає)
  5. CLEANUP_DAYS — поріг віку працює (стара серія архівується, свіжа ні)
  6. raw + thumbnail обидва видаляються
  7. chunked-commit: при 100 серіях commit вiдбувається ≥ 2 рази
  8. Партіальна стійкість: помилка os.remove не валить весь прогон

Запуск (потрібна реальна Postgres):
    CT_TEST_DATABASE_URI=postgresql://... \
        venv/Scripts/python -m pytest tests/test_cleanup_old_photos.py -v -m integration
"""

import json
import os
import time
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


CT_TEST_URI = os.environ.get('CT_TEST_DATABASE_URI', '')
INTEGRATION_AVAILABLE = CT_TEST_URI and not CT_TEST_URI.startswith('sqlite')


@pytest.mark.integration
@pytest.mark.skipif(not INTEGRATION_AVAILABLE,
                    reason="CT_TEST_DATABASE_URI not set (Postgres required)")
class TestCleanupOldPhotos:

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        from sqlalchemy import create_engine, text
        from app.camera_traps import background_tasks as bt
        from app.camera_traps import database as db_mod

        self.engine = create_engine(CT_TEST_URI)
        self.schema = f"test_archive_{uuid.uuid4().hex[:8]}"

        with self.engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {self.schema}"))
            conn.execute(text(f"SET search_path TO {self.schema}, public"))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.locations (
                    id SERIAL PRIMARY KEY, name TEXT,
                    latitude NUMERIC(10,5), longitude NUMERIC(10,5),
                    photo_count INTEGER DEFAULT 0,
                    visibility_level INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT NOW()
                )"""))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.observations (
                    id SERIAL PRIMARY KEY,
                    location_id INTEGER REFERENCES {self.schema}.locations(id),
                    series_start_time TIMESTAMP NOT NULL,
                    series_end_time TIMESTAMP NOT NULL,
                    photo_count INTEGER DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'pending',
                    uploaded_by_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )"""))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.upload_batches (
                    id VARCHAR(36) PRIMARY KEY,
                    location_id INTEGER REFERENCES {self.schema}.locations(id),
                    uploaded_by_id INTEGER NOT NULL,
                    status VARCHAR(20) DEFAULT 'uploading',
                    total_files INTEGER DEFAULT 0,
                    processed_files INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP,
                    error_message TEXT
                )"""))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.photos (
                    id SERIAL PRIMARY KEY,
                    observation_id INTEGER REFERENCES {self.schema}.observations(id),
                    upload_batch_id VARCHAR(36) REFERENCES {self.schema}.upload_batches(id),
                    original_filename TEXT NOT NULL,
                    system_filename TEXT UNIQUE NOT NULL,
                    sequence_number INTEGER,
                    captured_at TIMESTAMP NOT NULL,
                    status VARCHAR(20) DEFAULT 'uploaded',
                    identification_count INTEGER DEFAULT 0,
                    is_favorite BOOLEAN DEFAULT FALSE
                )"""))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.species (
                    id SERIAL PRIMARY KEY,
                    scientific_name VARCHAR(200) UNIQUE NOT NULL,
                    common_name_ua VARCHAR(200),
                    common_name_en VARCHAR(200),
                    category VARCHAR(50) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE NOT NULL,
                    kingdom VARCHAR(100), phylum VARCHAR(100),
                    class VARCHAR(100), order_rank VARCHAR(100),
                    family VARCHAR(100), genus VARCHAR(100),
                    establishment_means VARCHAR(100)
                )"""))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.identifications (
                    id SERIAL PRIMARY KEY,
                    photo_id INTEGER REFERENCES {self.schema}.photos(id) NOT NULL,
                    user_id INTEGER NOT NULL,
                    species_id INTEGER REFERENCES {self.schema}.species(id),
                    confidence_level INTEGER,
                    quantity INTEGER DEFAULT 1,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(photo_id, user_id)
                )"""))
            # Створюємо species з id=-2 = "Інше"
            conn.execute(text(f"""
                INSERT INTO {self.schema}.species (id, scientific_name, category)
                VALUES (-2, 'Other', 'unknown')
            """))

        # Підмінюємо session для background_tasks
        from sqlalchemy.orm import sessionmaker, scoped_session
        scoped_engine = create_engine(
            CT_TEST_URI,
            connect_args={'options': f'-csearch_path={self.schema},public'})
        Session = scoped_session(sessionmaker(bind=scoped_engine))
        monkeypatch.setattr(db_mod, 'get_ct_session', lambda: Session())
        monkeypatch.setattr(db_mod, 'close_ct_session', lambda: Session.remove())
        # background_tasks імпортує get/close_ct_session напряму — підмінити там теж
        monkeypatch.setattr(bt, 'get_ct_session', lambda: Session())
        monkeypatch.setattr(bt, 'close_ct_session', lambda: Session.remove())

        # Створюємо тимчасові директорії raw + thumbnails
        self.upload_root = str(tmp_path)
        self.raw_dir = os.path.join(self.upload_root, 'pending_photos', 'raw')
        self.thumb_dir = os.path.join(self.upload_root, 'pending_photos', 'thumbnails')
        os.makedirs(self.raw_dir)
        os.makedirs(self.thumb_dir)

        # Flask context
        from flask import Flask
        flask_app = Flask(__name__)
        flask_app.config['CAMERA_TRAP_CONFIG'] = {
            'UPLOAD_PATH': self.upload_root,
            'CLEANUP_DAYS': 0,  # одразу — щоб тестам не чекати
        }
        self.ctx = flask_app.app_context()
        self.ctx.push()

        # Зберігаємо для cleanup
        self._Session = Session
        self._scoped_engine = scoped_engine

        yield

        Session.remove()
        self.ctx.pop()
        with self.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        scoped_engine.dispose()
        self.engine.dispose()

    # ────────── Helpers ──────────

    def _mk_location(self):
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            return c.execute(text(
                f"INSERT INTO {self.schema}.locations(name,latitude,longitude) "
                f"VALUES('L',49.5,24.5) RETURNING id")).scalar()

    def _mk_obs(self, loc, status='completed'):
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            return c.execute(text(
                f"INSERT INTO {self.schema}.observations"
                f"(location_id, series_start_time, series_end_time, "
                f" uploaded_by_id, photo_count, status) "
                f"VALUES(:l, NOW(), NOW(), 1, 1, :s) RETURNING id"),
                {"l": loc, "s": status}).scalar()

    def _mk_photo(self, obs_id, is_favorite=False, status='completed',
                  filename=None, write_files=True):
        from sqlalchemy import text
        fn = filename or f"{uuid.uuid4().hex[:12]}.jpg"
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            pid = c.execute(text(
                f"INSERT INTO {self.schema}.photos"
                f"(observation_id, original_filename, system_filename, "
                f" captured_at, status, is_favorite) "
                f"VALUES(:o, :of, :sf, NOW(), :st, :fav) RETURNING id"),
                {"o": obs_id, "of": fn, "sf": fn, "st": status,
                 "fav": is_favorite}).scalar()
        if write_files:
            for d in (self.raw_dir, self.thumb_dir):
                with open(os.path.join(d, fn), 'wb') as f:
                    f.write(b'X' * 1024)
        return pid, fn

    def _mk_identification(self, photo_id, species_id=1, created_days_ago=10):
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            # Створюємо species якщо немає
            c.execute(text(f"""
                INSERT INTO {self.schema}.species (id, scientific_name, category)
                VALUES (:id, 'Sp' || :id, 'mammal')
                ON CONFLICT (id) DO NOTHING
            """), {"id": species_id})
            c.execute(text(f"""
                INSERT INTO {self.schema}.identifications
                    (photo_id, user_id, species_id, created_at)
                VALUES (:p, 1, :s, :ts)
            """), {"p": photo_id, "s": species_id,
                   "ts": datetime.utcnow() - timedelta(days=created_days_ago)})

    def _photo_status(self, photo_id):
        from sqlalchemy import text
        with self.engine.connect() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            return c.execute(text(
                f"SELECT status FROM {self.schema}.photos WHERE id=:id"),
                {"id": photo_id}).scalar()

    def _obs_status(self, obs_id):
        from sqlalchemy import text
        with self.engine.connect() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            return c.execute(text(
                f"SELECT status FROM {self.schema}.observations WHERE id=:id"),
                {"id": obs_id}).scalar()

    # ────────── Tests ──────────

    def test_basic_archive_removes_both_files(self):
        """Базовий сценарій: фото та обидва файли видаляються, БД → archived."""
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        obs = self._mk_obs(loc)
        pid, fn = self._mk_photo(obs)
        self._mk_identification(pid, species_id=1)

        result = cleanup_old_photos()
        assert result['success'] is True
        assert result['photos_deleted'] == 1
        assert result['observations_archived'] == 1
        assert self._photo_status(pid) == 'archived'
        assert self._obs_status(obs) == 'archived'
        # Файли видалено (raw + thumb)
        assert not os.path.exists(os.path.join(self.raw_dir, fn))
        assert not os.path.exists(os.path.join(self.thumb_dir, fn))

    def test_favorite_photo_untouched_within_archived_series(self):
        """is_favorite=TRUE — фото та обидва його файли лишаються незмінні,
        навіть якщо серія в цілому йде в archived (бо інші її фото архівовано
        і всі рештки = архівоване ∪ favorite).
        Семантика: favorite-фото — це «зразок для збереження»; серія, дані
        якої вже оброблено, переходить в archived, але фото-зразок цілий.
        """
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        obs = self._mk_obs(loc)
        pid_fav, fn_fav = self._mk_photo(obs, is_favorite=True)
        pid_normal, fn_normal = self._mk_photo(obs)
        self._mk_identification(pid_fav, species_id=1)
        self._mk_identification(pid_normal, species_id=1)

        cleanup_old_photos()
        # FAVORITE-фото — повністю недоторкане: статус 'completed', файли цілі
        assert self._photo_status(pid_fav) == 'completed'
        assert os.path.exists(os.path.join(self.raw_dir, fn_fav))
        assert os.path.exists(os.path.join(self.thumb_dir, fn_fav))
        # Звичайне — архівоване, файли видалено
        assert self._photo_status(pid_normal) == 'archived'
        assert not os.path.exists(os.path.join(self.raw_dir, fn_normal))
        assert not os.path.exists(os.path.join(self.thumb_dir, fn_normal))
        # Сама серія перейшла в archived (всі рештки = archived ∪ favorite)
        assert self._obs_status(obs) == 'archived'

    def test_species_other_never_archived(self):
        """species_id=-2 (категорія 'Інше') — серія не архівується взагалі."""
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        obs = self._mk_obs(loc)
        pid, fn = self._mk_photo(obs)
        self._mk_identification(pid, species_id=-2)  # "Інше"

        result = cleanup_old_photos()
        assert result['photos_deleted'] == 0
        assert self._photo_status(pid) == 'completed'
        assert os.path.exists(os.path.join(self.raw_dir, fn))

    def test_observation_archived_only_when_all_photos_archived(self):
        """observation.status='archived' лише коли усі фото archived/favorite."""
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        obs = self._mk_obs(loc)
        pid_a, _ = self._mk_photo(obs)
        pid_b, _ = self._mk_photo(obs)
        self._mk_identification(pid_a, species_id=1)
        self._mk_identification(pid_b, species_id=1)

        cleanup_old_photos()
        # Обидва архівовані → серія теж
        assert self._photo_status(pid_a) == 'archived'
        assert self._photo_status(pid_b) == 'archived'
        assert self._obs_status(obs) == 'archived'

    def test_threshold_skips_recent_observations(self):
        """Свіжі ідентифікації (молодші CLEANUP_DAYS) — не архівуються."""
        from flask import current_app
        from app.camera_traps.background_tasks import cleanup_old_photos
        # Виставляємо поріг 7 днів — свіжіша ідентифікація не повинна потрапити
        current_app.config['CAMERA_TRAP_CONFIG']['CLEANUP_DAYS'] = 7

        loc = self._mk_location()
        obs_old = self._mk_obs(loc)
        obs_new = self._mk_obs(loc)
        pid_old, _ = self._mk_photo(obs_old)
        pid_new, _ = self._mk_photo(obs_new)
        self._mk_identification(pid_old, species_id=1, created_days_ago=30)
        self._mk_identification(pid_new, species_id=1, created_days_ago=2)

        cleanup_old_photos()
        assert self._photo_status(pid_old) == 'archived'
        assert self._photo_status(pid_new) == 'completed'

    def test_chunked_commit_with_many_observations(self):
        """≥ CHUNK_OBS_SIZE серій → ≥ 2 commit-и; усі фото архівуються."""
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        # 120 серій по одному фото = ≥ 2 чанки (CHUNK_OBS_SIZE=50)
        pids = []
        for i in range(120):
            obs = self._mk_obs(loc)
            pid, _ = self._mk_photo(obs)
            self._mk_identification(pid, species_id=1)
            pids.append(pid)

        result = cleanup_old_photos()
        assert result['success'] is True
        assert result['photos_deleted'] == 120
        assert result['observations_archived'] == 120
        # Усі фото справді архівовані
        for pid in pids:
            assert self._photo_status(pid) == 'archived'

    def test_os_remove_failure_does_not_break_db_state(self):
        """Якщо os.remove падає (mock), БД має правильний archived-стан,
        а файл стає orphan-сиротою (підбере новий cleanup-модуль)."""
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        obs = self._mk_obs(loc)
        pid, fn = self._mk_photo(obs)
        self._mk_identification(pid, species_id=1)

        original_remove = os.remove
        def flaky_remove(p):
            if fn in p:
                raise OSError("Simulated disk error")
            original_remove(p)

        with patch('app.camera_traps.background_tasks.os.remove',
                   side_effect=flaky_remove):
            result = cleanup_old_photos()

        # БД — у правильному стані (commit пройшов до видалення файлів)
        assert result['success'] is True
        assert self._photo_status(pid) == 'archived'
        assert self._obs_status(obs) == 'archived'
        # Файли лишились на диску — стануть orphan-сиротами
        assert os.path.exists(os.path.join(self.raw_dir, fn))
        assert os.path.exists(os.path.join(self.thumb_dir, fn))

    def test_returns_partial_counts_on_failure(self):
        """При збої вертає кількість УСПІШНО архівованих, не загальну."""
        from app.camera_traps.background_tasks import cleanup_old_photos
        loc = self._mk_location()
        # 60 серій → 2 чанки
        for i in range(60):
            obs = self._mk_obs(loc)
            pid, _ = self._mk_photo(obs)
            self._mk_identification(pid, species_id=1)

        # Імітуємо помилку у session.commit на ДРУГОМУ виклику
        from app.camera_traps import background_tasks as bt
        original_session_factory = bt.get_ct_session
        call_count = {'commits': 0}
        session = original_session_factory()
        original_commit = session.commit
        def failing_commit():
            call_count['commits'] += 1
            if call_count['commits'] == 2:
                raise RuntimeError("Simulated commit failure")
            return original_commit()
        session.commit = failing_commit
        with patch.object(bt, 'get_ct_session', return_value=session):
            result = cleanup_old_photos()

        # Перший чанк (50 серій) пройшов commit → у counts
        # Другий упав → не у counts. Результат: 50.
        assert result['success'] is False
        assert result['photos_deleted'] == 50
        assert result['observations_archived'] == 50
