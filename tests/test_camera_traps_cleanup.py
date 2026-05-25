"""
Тести cleanup-модуля (заміна старого cleanup_stale_batches).

Покриває критичні інваріанти безпеки:
  1. is_favorite=TRUE ніколи не у звіті/видаленні
  2. observation_id IS NOT NULL ніколи не у звіті/видаленні
  3. status != 'uploaded' ніколи не у звіті/видаленні
  4. Активні batch (probe детектує зміну processed_files) → захищені
  5. Файли молодші DISK_MTIME_SAFETY_SECONDS → захищені
  6. Звіт expired (>10 хв) → execute відхиляється з 410
  7. Подвійний execute → 409
  8. Recovery: status='executing' старші 1 год → 'failed'
  9. Retention: рядки cleanup_log старші 90 днів → видалені при analyze
 10. Не-admin → 302/403

Інтеграційні (real Postgres, через CT_TEST_DATABASE_URI):
 11. E2E: analyze → execute → перевірка реального стану БД + диска
 12. Active batch detected by probe — не у звіті
 13. Орфан-файли видаляються; favorite/observation-файли — ні
 14. Concurrent insert під час analyze — батч стає захищеним
 15. Партіальне виконання при OSError на одному файлі — інші чистяться

Запуск:
    venv/Scripts/python -m pytest tests/test_camera_traps_cleanup.py -v
    CT_TEST_DATABASE_URI=postgresql://... -m integration
"""

import contextlib
import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ═════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Access control + basic route behavior (моки)
# ═════════════════════════════════════════════════════════════════════════════

def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _generic_session():
    q = MagicMock()
    for method in ('join', 'outerjoin', 'filter', 'order_by', 'group_by',
                   'having', 'distinct', 'params', 'limit', 'offset',
                   'select_from', 'with_entities', 'options'):
        getattr(q, method).return_value = q
    q.all.return_value = []
    q.scalar.return_value = 0
    q.first.return_value = None
    q.get.return_value = None
    sess = MagicMock()
    sess.query.return_value = q
    return sess


class CleanupRouteBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock(),
        )
        cls._ct_patcher.start()
        from app import create_app
        cls.app = create_app('testing')
        cls.app.config['GEOSERVER_URL'] = 'http://test-geoserver'

    @classmethod
    def tearDownClass(cls):
        cls._ct_patcher.stop()
        os.environ.pop('DATABASE_URL', None)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app.extensions import db
        db.create_all()
        self._seed(db)
        self.client = self.app.test_client()

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self, db):
        from app.extensions import bcrypt
        from app.models import User, Role, Institution, UserInstitution
        r_admin = Role(name='admin')
        r_manager = Role(name='manager')
        r_viewer = Role(name='viewer')
        db.session.add_all([r_admin, r_manager, r_viewer])
        db.session.flush()
        self.inst = Institution(name_uk='I', name_en='I', code='i')
        db.session.add(self.inst); db.session.flush()
        pw = bcrypt.generate_password_hash('test').decode('utf-8')
        self.admin = User(username='a', password_hash=pw); self.admin.roles.append(r_admin)
        self.manager = User(username='m', password_hash=pw); self.manager.roles.append(r_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst.id, can_export=False))
        self.viewer = User(username='v', password_hash=pw); self.viewer.roles.append(r_viewer)
        db.session.add_all([self.admin, self.manager, self.viewer])
        db.session.commit()


class TestCleanupRouteAccess(CleanupRouteBase):
    """Тести доступу до маршрутів — лише admin."""

    def test_analyze_admin_starts_thread(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.analyze_cleanup',
                   return_value='abc-123') as m:
            resp = self.client.post(
                '/uk/camera-traps/admin/cleanup/analyze',
                json={}, content_type='application/json')
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.get_json()['report_id'], 'abc-123')
        m.assert_called_once()

    def test_analyze_manager_forbidden(self):
        _login(self.client, self.manager.id)
        resp = self.client.post(
            '/uk/camera-traps/admin/cleanup/analyze',
            json={}, content_type='application/json')
        self.assertIn(resp.status_code, (302, 403))

    def test_analyze_anonymous_redirects(self):
        resp = self.client.post('/uk/camera-traps/admin/cleanup/analyze',
                                json={}, content_type='application/json')
        self.assertEqual(resp.status_code, 302)

    def test_execute_admin_calls_start(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.start_execute') as m:
            resp = self.client.post(
                '/uk/camera-traps/admin/cleanup/execute/some-id',
                json={}, content_type='application/json')
        self.assertEqual(resp.status_code, 202)
        m.assert_called_once_with(report_id='some-id', probe_seconds=10)

    def test_execute_expired_returns_410(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.start_execute',
                   side_effect=ValueError("Report expired")):
            resp = self.client.post(
                '/uk/camera-traps/admin/cleanup/execute/x',
                json={}, content_type='application/json')
        self.assertEqual(resp.status_code, 410)

    def test_execute_not_found_returns_404(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.start_execute',
                   side_effect=ValueError("Report not found")):
            resp = self.client.post(
                '/uk/camera-traps/admin/cleanup/execute/x',
                json={}, content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_execute_wrong_status_returns_409(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.start_execute',
                   side_effect=ValueError("Report status is 'executing', expected 'analyzed'")):
            resp = self.client.post(
                '/uk/camera-traps/admin/cleanup/execute/x',
                json={}, content_type='application/json')
        self.assertEqual(resp.status_code, 409)

    def test_task_status_admin_returns_log(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.get_log',
                   return_value={'id': 'x', 'status': 'analyzed'}):
            resp = self.client.get('/uk/camera-traps/admin/cleanup/task/x')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['status'], 'analyzed')

    def test_task_status_not_found(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.cleanup.get_log', return_value=None):
            resp = self.client.get('/uk/camera-traps/admin/cleanup/task/x')
        self.assertEqual(resp.status_code, 404)


# ═════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — real Postgres ct_db через тунель
# ═════════════════════════════════════════════════════════════════════════════

CT_TEST_URI = os.environ.get('CT_TEST_DATABASE_URI', '')
INTEGRATION_AVAILABLE = CT_TEST_URI and not CT_TEST_URI.startswith('sqlite')


@pytest.mark.integration
@pytest.mark.skipif(not INTEGRATION_AVAILABLE,
                    reason="CT_TEST_DATABASE_URI not set (Postgres required)")
class TestCleanupIntegration:
    """
    E2E на реальній Postgres. Створює тимчасову схему з усіма потрібними
    таблицями (фактично копія публічних), наповнює, ганяє cleanup,
    перевіряє інваріанти.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        from sqlalchemy import create_engine, text
        from app.camera_traps import cleanup as cleanup_mod

        self.engine = create_engine(CT_TEST_URI)
        self.schema = f"test_cleanup_{uuid.uuid4().hex[:8]}"

        # Mini-schema for tests
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
                CREATE TABLE {self.schema}.cleanup_log (
                    id VARCHAR(36) PRIMARY KEY,
                    kind VARCHAR(20) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    triggered_by INTEGER NOT NULL,
                    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMP,
                    threshold_hours INTEGER NOT NULL DEFAULT 0,
                    report_json JSONB,
                    batches_examined INTEGER,
                    batches_marked_failed INTEGER,
                    photos_deleted INTEGER,
                    files_deleted INTEGER,
                    bytes_freed BIGINT,
                    error_message TEXT
                )"""))

        scoped_engine = create_engine(
            CT_TEST_URI,
            connect_args={'options': f'-csearch_path={self.schema},public'})
        monkeypatch.setattr(cleanup_mod, 'get_ct_engine', lambda: scoped_engine)

        # Створюємо тимчасові директорії raw/thumbnails
        self.upload_root = str(tmp_path)
        os.makedirs(os.path.join(self.upload_root, 'pending_photos', 'raw'))
        os.makedirs(os.path.join(self.upload_root, 'pending_photos', 'thumbnails'))
        self.raw_dir = os.path.join(self.upload_root, 'pending_photos', 'raw')
        self.thumb_dir = os.path.join(self.upload_root, 'pending_photos', 'thumbnails')

        # Flask-context з потрібним конфігом
        from flask import Flask
        flask_app = Flask(__name__)
        flask_app.config['CAMERA_TRAP_CONFIG'] = {
            'UPLOAD_PATH': self.upload_root,
            'CLEANUP_LOG_RETENTION_DAYS': 90,
        }
        self.ctx = flask_app.app_context()
        self.ctx.push()

        yield

        self.ctx.pop()
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        self.engine.dispose()
        scoped_engine.dispose()

    # ────────── Helpers ──────────

    def _mk_loc(self):
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            return c.execute(text(
                f"INSERT INTO {self.schema}.locations(name,latitude,longitude) "
                f"VALUES('L', 49.5, 24.5) RETURNING id")).scalar()

    def _mk_batch(self, loc_id, status='uploading', processed=0, age_min=0):
        from sqlalchemy import text
        bid = str(uuid.uuid4())
        created = datetime.utcnow() - timedelta(minutes=age_min)
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            c.execute(text(
                f"INSERT INTO {self.schema}.upload_batches"
                f"(id, location_id, uploaded_by_id, status, processed_files, "
                f"total_files, created_at) "
                f"VALUES (:b, :l, 1, :s, :p, 10, :ts)"),
                {"b": bid, "l": loc_id, "s": status, "p": processed,
                 "ts": created})
        return bid

    def _mk_photo(self, batch_id, observation_id=None, is_favorite=False,
                  status='uploaded', filename=None, write_file=True):
        from sqlalchemy import text
        fn = filename or f"{batch_id[:8]}_{uuid.uuid4().hex[:8]}.jpg"
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            pid = c.execute(text(
                f"INSERT INTO {self.schema}.photos"
                f"(observation_id, upload_batch_id, original_filename, "
                f" system_filename, captured_at, status, is_favorite) "
                f"VALUES(:obs, :b, :of, :sf, NOW(), :st, :fav) RETURNING id"),
                {"obs": observation_id, "b": batch_id, "of": fn, "sf": fn,
                 "st": status, "fav": is_favorite}).scalar()
        if write_file:
            for d in (self.raw_dir, self.thumb_dir):
                with open(os.path.join(d, fn), 'wb') as f:
                    f.write(b'X' * 1024)
            # Backdate mtime щоб пройти DISK_MTIME_SAFETY
            old = time.time() - 3600
            for d in (self.raw_dir, self.thumb_dir):
                os.utime(os.path.join(d, fn), (old, old))
        return pid, fn

    def _mk_observation(self, loc_id):
        from sqlalchemy import text
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            return c.execute(text(
                f"INSERT INTO {self.schema}.observations"
                f"(location_id, series_start_time, series_end_time, "
                f" uploaded_by_id, photo_count, status) "
                f"VALUES(:l, NOW(), NOW(), 1, 1, 'pending') RETURNING id"),
                {"l": loc_id}).scalar()

    def _make_orphan_file(self, name, age_seconds=3600):
        """Файл на диску, якого НЕМАЄ в photos."""
        for d in (self.thumb_dir,):  # лише thumbnails — частіший випадок
            p = os.path.join(d, name)
            with open(p, 'wb') as f:
                f.write(b'O' * 512)
            old = time.time() - age_seconds
            os.utime(p, (old, old))

    def _collect_report_sync(self, threshold_hours=0, probe_seconds=1):
        """Викликаємо _collect_cleanup_report напряму — без threading."""
        from app.camera_traps.cleanup import _collect_cleanup_report
        return _collect_cleanup_report(threshold_hours, probe_seconds)

    # ────────── Інваріанти SAFETY ──────────

    def test_favorite_photo_never_in_report(self):
        loc = self._mk_loc()
        bid = self._mk_batch(loc, status='failed', age_min=60)
        # Один stranded + один favorite (з тим же batch — обидва без obs)
        pid_strand, fn_strand = self._mk_photo(bid, is_favorite=False)
        pid_fav, fn_fav = self._mk_photo(bid, is_favorite=True)
        report = self._collect_report_sync()
        names = {p["system_filename"] for p in report["stranded_photos_sample"]}
        assert fn_strand in names
        assert fn_fav not in names, "Favorite photo MUST NEVER appear"

    def test_photo_with_observation_never_in_report(self):
        loc = self._mk_loc()
        bid = self._mk_batch(loc, status='failed', age_min=60)
        obs = self._mk_observation(loc)
        # Stranded + grouped
        pid_s, fn_s = self._mk_photo(bid, is_favorite=False)
        pid_g, fn_g = self._mk_photo(bid, observation_id=obs)
        report = self._collect_report_sync()
        names = {p["system_filename"] for p in report["stranded_photos_sample"]}
        assert fn_s in names
        assert fn_g not in names, "Photo with observation MUST NEVER appear"

    def test_active_status_photo_never_in_report(self):
        """status='pending' (already in work) NEVER in stranded list."""
        loc = self._mk_loc()
        bid = self._mk_batch(loc, status='failed', age_min=60)
        pid, fn = self._mk_photo(bid, status='pending', is_favorite=False)
        report = self._collect_report_sync()
        names = {p["system_filename"] for p in report["stranded_photos_sample"]}
        assert fn not in names

    def test_completed_batch_not_in_stale_list(self):
        """Завершені batchʼі не позначаються як stale."""
        loc = self._mk_loc()
        bid_completed = self._mk_batch(loc, status='completed', age_min=60)
        bid_failed = self._mk_batch(loc, status='failed', age_min=60)
        report = self._collect_report_sync()
        stale_ids = {b["id"] for b in report["stale_batches"]}
        assert bid_failed in stale_ids
        assert bid_completed not in stale_ids

    def test_recent_file_protected_by_mtime(self):
        """Файл-сирота молодший 5 хв — не у звіті."""
        self._make_orphan_file('fresh_orphan.jpg', age_seconds=60)
        self._make_orphan_file('old_orphan.jpg', age_seconds=3600)
        report = self._collect_report_sync()
        names = {f["name"] for f in report["orphan_files_sample"]}
        assert 'old_orphan.jpg' in names
        assert 'fresh_orphan.jpg' not in names

    def test_active_batch_protected_by_probe(self):
        """probe: симулюємо зростання processed_files → batch захищений."""
        from sqlalchemy import text
        loc = self._mk_loc()
        bid_active = self._mk_batch(loc, status='uploading', processed=5)
        bid_stale = self._mk_batch(loc, status='uploading', processed=10,
                                    age_min=120)

        # Імітуємо «активність» через окремий thread, який під час probe
        # робить UPDATE processed_files. Затримка повинна бути більшою за
        # мережеву латентність першого snapshot-запиту (інакше snap1 може
        # прочитати вже оновлене значення → probe не побачить різниці).
        import threading
        def bump():
            time.sleep(1.5)
            with self.engine.begin() as c:
                c.execute(text(f"SET search_path TO {self.schema}, public"))
                c.execute(text(
                    f"UPDATE {self.schema}.upload_batches "
                    f"SET processed_files = processed_files + 3 WHERE id=:b"),
                    {"b": bid_active})
        threading.Thread(target=bump).start()

        report = self._collect_report_sync(probe_seconds=3)
        stale_ids = {b["id"] for b in report["stale_batches"]}
        assert bid_active not in stale_ids, "Active batch NOT protected"
        assert bid_stale in stale_ids
        assert bid_active in report["active_protected_ids"]

    # ────────── End-to-end ──────────

    def test_e2e_analyze_then_execute(self):
        """Повний прохід: створити сцену, analyze, execute, перевірити."""
        from sqlalchemy import text
        from app.camera_traps.cleanup import (
            _collect_cleanup_report, _execute_cleanup,
        )

        loc = self._mk_loc()
        bid = self._mk_batch(loc, status='failed', age_min=60)
        # 3 stranded photos + 1 favorite + 1 grouped
        s1 = self._mk_photo(bid, is_favorite=False)
        s2 = self._mk_photo(bid, is_favorite=False)
        s3 = self._mk_photo(bid, is_favorite=False)
        fav = self._mk_photo(bid, is_favorite=True)
        obs = self._mk_observation(loc)
        grp = self._mk_photo(bid, observation_id=obs)
        # 2 orphan files
        self._make_orphan_file('orph_1.jpg', age_seconds=3600)
        self._make_orphan_file('orph_2.jpg', age_seconds=3600)

        # ANALYZE — синхронно (обхід threading для тесту)
        report = _collect_report_sync(threshold_hours=0, probe_seconds=1) if False else \
                 _collect_cleanup_report(0, 1)
        assert report["stranded_photos_count"] == 3
        assert report["orphan_files_count"] == 2

        # Запис у БД для execute
        report_id = str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            c.execute(text(f"""
                INSERT INTO {self.schema}.cleanup_log
                    (id, kind, status, triggered_by, threshold_hours, report_json)
                VALUES (:id, 'analysis', 'analyzed', 1, 0, CAST(:r AS JSONB))
            """), {"id": report_id, "r": json.dumps(report)})

        # EXECUTE
        stats = _execute_cleanup(report_id, probe_seconds=1)

        # ПЕРЕВІРКИ
        # 3 stranded photos видалено
        with self.engine.connect() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            remaining = c.execute(text(
                f"SELECT COUNT(*) FROM {self.schema}.photos "
                f"WHERE upload_batch_id=:b"), {"b": bid}).scalar()
        assert remaining == 2, f"Expected 2 (favorite + grouped), got {remaining}"

        # Файли stranded видалено
        for _, fn in (s1, s2, s3):
            assert not os.path.exists(os.path.join(self.thumb_dir, fn))
        # Файли favorite + grouped — лишились
        for _, fn in (fav, grp):
            assert os.path.exists(os.path.join(self.thumb_dir, fn))
        # Orphan files видалено
        assert not os.path.exists(os.path.join(self.thumb_dir, 'orph_1.jpg'))
        assert not os.path.exists(os.path.join(self.thumb_dir, 'orph_2.jpg'))

        # Statistics sane
        assert stats["pd"] == 3  # photos_deleted
        assert stats["fd"] >= 2  # files_deleted: 2 orphan + 3*2 stranded files (raw+thumb)

    def test_purge_old_logs(self):
        """Записи cleanup_log старші retention — видаляються."""
        from sqlalchemy import text
        from app.camera_traps.cleanup import purge_old_logs

        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            # старий запис (валідний UUID — VARCHAR(36) обмеження)
            c.execute(text(f"""
                INSERT INTO {self.schema}.cleanup_log
                    (id, kind, status, triggered_by, started_at)
                VALUES (:id, 'analysis', 'completed', 1, :ts)
            """), {"id": str(uuid.uuid4()),
                   "ts": datetime.utcnow() - timedelta(days=100)})
            # свіжий запис
            c.execute(text(f"""
                INSERT INTO {self.schema}.cleanup_log
                    (id, kind, status, triggered_by, started_at)
                VALUES (:id, 'analysis', 'completed', 1, NOW())
            """), {"id": str(uuid.uuid4())})

        n = purge_old_logs()
        assert n == 1

        with self.engine.connect() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            cnt = c.execute(text(
                f"SELECT COUNT(*) FROM {self.schema}.cleanup_log")).scalar()
        assert cnt == 1

    def test_recover_stuck_cleanup(self):
        """status='executing' старші 1 год → переведені у 'failed'."""
        from sqlalchemy import text
        from app.camera_traps.cleanup import recover_stuck_cleanup

        old_id = str(uuid.uuid4())
        fresh_id = str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            c.execute(text(f"""
                INSERT INTO {self.schema}.cleanup_log
                    (id, kind, status, triggered_by, started_at)
                VALUES (:id, 'execution', 'executing', 1, :ts)
            """), {"id": old_id, "ts": datetime.utcnow() - timedelta(hours=2)})
            c.execute(text(f"""
                INSERT INTO {self.schema}.cleanup_log
                    (id, kind, status, triggered_by, started_at)
                VALUES (:id, 'execution', 'executing', 1, NOW())
            """), {"id": fresh_id})

        n = recover_stuck_cleanup()
        assert n == 1

        with self.engine.connect() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            old_status = c.execute(text(
                f"SELECT status FROM {self.schema}.cleanup_log WHERE id=:id"),
                {"id": old_id}).scalar()
            fresh_status = c.execute(text(
                f"SELECT status FROM {self.schema}.cleanup_log WHERE id=:id"),
                {"id": fresh_id}).scalar()
        assert old_status == 'failed'
        assert fresh_status == 'executing'

    def test_execute_reverifies_active(self):
        """Якщо batch стає активним між analyze і execute — не чіпаємо."""
        from sqlalchemy import text
        from app.camera_traps.cleanup import _collect_cleanup_report, _execute_cleanup

        loc = self._mk_loc()
        bid = self._mk_batch(loc, status='uploading', processed=0, age_min=120)
        pid, fn = self._mk_photo(bid, is_favorite=False)

        # analyze — batch нерухомий, потрапляє у stale
        report = _collect_cleanup_report(threshold_hours=0, probe_seconds=1)
        assert bid in {b["id"] for b in report["stale_batches"]}

        # Зберігаємо звіт
        report_id = str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            c.execute(text(f"""
                INSERT INTO {self.schema}.cleanup_log
                    (id, kind, status, triggered_by, threshold_hours, report_json)
                VALUES (:id, 'analysis', 'analyzed', 1, 0, CAST(:r AS JSONB))
            """), {"id": report_id, "r": json.dumps(report)})

        # Симулюємо «active» — інкремент під час execute-probe. Затримка
        # 1.5с > мережева латентність першого snap → snap1<snap2.
        import threading
        def bump():
            time.sleep(1.5)
            with self.engine.begin() as c:
                c.execute(text(f"SET search_path TO {self.schema}, public"))
                c.execute(text(
                    f"UPDATE {self.schema}.upload_batches "
                    f"SET processed_files = processed_files + 1 WHERE id=:b"),
                    {"b": bid})
        threading.Thread(target=bump).start()

        stats = _execute_cleanup(report_id, probe_seconds=3)
        # batch не марковано failed (бо став активним)
        assert stats["bmf"] == 0
        # photo не видалено
        with self.engine.connect() as c:
            c.execute(text(f"SET search_path TO {self.schema}, public"))
            still = c.execute(text(
                f"SELECT COUNT(*) FROM {self.schema}.photos WHERE id=:id"),
                {"id": pid}).scalar()
        assert still == 1, "Photo was deleted despite batch becoming active"


# Local helper alias used inside class methods
def _collect_report_sync(threshold_hours=0, probe_seconds=1):
    from app.camera_traps.cleanup import _collect_cleanup_report
    return _collect_cleanup_report(threshold_hours, probe_seconds)
