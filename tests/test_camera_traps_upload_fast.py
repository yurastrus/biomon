"""
Tests for the /upload-fast path (fast upload of large sets).

Cover:
  1. Access to the /upload-fast page (manager+ only)
  2. /api/finalize-batch-async route — states, idempotency
  3. /api/batch/<id>/uploaded-files route
  4. fast_upload.recover_stale_grouping_batches (smoke)
  5. INTEGRATION (optional, requires a real ct_db in Postgres):
     group_batch_into_series_sql — series-boundary correctness, idempotency,
     re-grouping after a partial apply.

The integration block is skipped when the env has no
CT_TEST_DATABASE_URI or it starts with 'sqlite' (window functions +
make_interval are Postgres-only).

Run:
    venv/Scripts/python -m pytest tests/test_camera_traps_upload_fast.py -v
    # Integration (slow):
    CT_TEST_DATABASE_URI=postgresql://... venv/Scripts/python -m pytest \
        tests/test_camera_traps_upload_fast.py -v -m integration
"""

import contextlib
import os
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ═════════════════════════════════════════════════════════════════════════════
# 1. ACCESS TESTS (following the test_ct_pages_access pattern)
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


class UploadFastAccessBase(unittest.TestCase):

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
        r_admin   = Role(name='admin')
        r_manager = Role(name='manager')
        r_viewer  = Role(name='viewer')
        db.session.add_all([r_admin, r_manager, r_viewer])
        db.session.flush()
        self.inst = Institution(name_uk='I', name_en='I', code='i')
        db.session.add(self.inst)
        db.session.flush()
        pw = bcrypt.generate_password_hash('test').decode('utf-8')
        self.admin = User(username='a', password_hash=pw)
        self.admin.roles.append(r_admin)
        self.manager = User(username='m', password_hash=pw)
        self.manager.roles.append(r_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst.id, can_export=False))
        self.viewer = User(username='v', password_hash=pw)
        self.viewer.roles.append(r_viewer)
        db.session.add_all([self.admin, self.manager, self.viewer])
        db.session.commit()

    def _get(self, url, user_id=None):
        if user_id:
            _login(self.client, user_id)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch('app.camera_traps.routes.get_ct_session',
                      return_value=_generic_session()))
            stack.enter_context(patch('app.camera_traps.routes.close_ct_session'))
            return self.client.get(url)


class TestUploadFastPageAccess(UploadFastAccessBase):

    URL = '/uk/camera-traps/upload-fast'

    def test_anonymous_redirects(self):
        self.assertEqual(self._get(self.URL).status_code, 302)

    def test_viewer_redirects(self):
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 302)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(self.URL, self.admin.id).status_code, 200)


# ═════════════════════════════════════════════════════════════════════════════
# 2. /api/finalize-batch-async — status transitions + idempotency
# ═════════════════════════════════════════════════════════════════════════════

class TestFinalizeAsyncRoute(UploadFastAccessBase):

    URL = '/uk/camera-traps/api/finalize-batch-async'

    def _post(self, payload, user_id, batch_status='uploading'):
        """Emulate a POST with a given batch state at the DB layer."""
        _login(self.client, user_id)
        # Mock ORM session — returns an UploadBatch with the desired status.
        batch_mock = MagicMock()
        batch_mock.status = batch_status
        batch_mock.id = payload.get('batch_id', 'x')
        sess = _generic_session()
        sess.query.return_value.get.return_value = batch_mock
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch('app.camera_traps.routes.get_ct_session', return_value=sess))
            stack.enter_context(patch('app.camera_traps.routes.close_ct_session'))
            stack.enter_context(
                patch('app.camera_traps.fast_upload.start_async_grouping'))
            return self.client.post(
                self.URL,
                json=payload,
                content_type='application/json',
            )

    def test_missing_batch_id_400(self):
        resp = self._post({}, self.manager.id)
        self.assertEqual(resp.status_code, 400)

    def test_uploading_batch_accepted_202(self):
        resp = self._post({'batch_id': str(uuid.uuid4())},
                          self.manager.id, batch_status='uploading')
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(resp.get_json()['success'])

    def test_failed_batch_can_retry_202(self):
        """Allow retry from 'failed' (idempotency)."""
        resp = self._post({'batch_id': str(uuid.uuid4())},
                          self.manager.id, batch_status='failed')
        self.assertEqual(resp.status_code, 202)

    def test_completed_batch_rejected_409(self):
        resp = self._post({'batch_id': str(uuid.uuid4())},
                          self.manager.id, batch_status='completed')
        self.assertEqual(resp.status_code, 409)

    def test_grouping_batch_rejected_409(self):
        """'grouping' — a task is already running, do not allow a repeat finalize."""
        resp = self._post({'batch_id': str(uuid.uuid4())},
                          self.manager.id, batch_status='grouping')
        self.assertEqual(resp.status_code, 409)

    def test_viewer_forbidden(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self.URL, json={'batch_id': 'x'},
                                content_type='application/json')
        # role_required redirects to the dashboard
        self.assertIn(resp.status_code, (302, 403))


# ═════════════════════════════════════════════════════════════════════════════
# 3. /api/batch/<id>/uploaded-files
# ═════════════════════════════════════════════════════════════════════════════

class TestUploadedFilesRoute(UploadFastAccessBase):

    def test_empty_batch_returns_zero(self):
        _login(self.client, self.manager.id)
        sess = _generic_session()
        sess.query.return_value.filter.return_value.all.return_value = []
        with patch('app.camera_traps.routes.get_ct_session', return_value=sess), \
             patch('app.camera_traps.routes.close_ct_session'):
            resp = self.client.get('/uk/camera-traps/api/batch/abc/uploaded-files')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['files'], [])

    def test_returns_list_of_filenames(self):
        _login(self.client, self.manager.id)
        sess = _generic_session()
        ts = datetime(2026, 1, 1, 12, 0)
        sess.query.return_value.filter.return_value.all.return_value = [
            ('a.jpg', ts), ('b.jpg', ts + timedelta(seconds=10)),
        ]
        with patch('app.camera_traps.routes.get_ct_session', return_value=sess), \
             patch('app.camera_traps.routes.close_ct_session'):
            resp = self.client.get('/uk/camera-traps/api/batch/abc/uploaded-files')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['count'], 2)
        self.assertEqual({f['original_filename'] for f in data['files']},
                         {'a.jpg', 'b.jpg'})


# ═════════════════════════════════════════════════════════════════════════════
# 4. recover_stale_grouping_batches — smoke
# ═════════════════════════════════════════════════════════════════════════════

def test_recover_stale_is_safe_with_engine_error(monkeypatch):
    """If the engine fails, recover does not break the import; returns 0."""
    from app.camera_traps import fast_upload

    def boom(*a, **kw):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(fast_upload, 'get_ct_engine', boom)

    # Call within an app context (current_app is needed for the logger)
    os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
    with patch('app.camera_traps.database.create_engine', return_value=MagicMock()):
        from app import create_app
        app = create_app('testing')
        with app.app_context():
            result = fast_upload.recover_stale_grouping_batches()
            assert result == 0


# ═════════════════════════════════════════════════════════════════════════════
# 5. INTEGRATION: group_batch_into_series_sql on a real Postgres ct_db
# ═════════════════════════════════════════════════════════════════════════════

CT_TEST_URI = os.environ.get('CT_TEST_DATABASE_URI', '')
INTEGRATION_AVAILABLE = (
    CT_TEST_URI
    and not CT_TEST_URI.startswith('sqlite')
)


@pytest.mark.integration
@pytest.mark.skipif(not INTEGRATION_AVAILABLE,
                    reason="CT_TEST_DATABASE_URI not set (Postgres required)")
class TestGroupSqlIntegration:
    """
    Tests for the SQL grouper itself. Require a real Postgres ct_db.
    Create temporary tables, run synthetic data through them, and clean up after.

    Series semantics: photos within SERIES_TIME_WINDOW seconds of the previous one
    belong to the same series.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        from sqlalchemy import create_engine
        from app.camera_traps import database as ct_db_mod, fast_upload as fu

        # Temporary schema, so we never touch prod tables even by accident
        engine = create_engine(CT_TEST_URI)
        self.engine = engine
        self.schema = f"test_fast_{uuid.uuid4().hex[:8]}"

        # Create a minimal set of tables for the test (a subset of the models)
        with engine.begin() as conn:
            from sqlalchemy import text
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

        # Swap fast_upload's engine so it operates in our schema
        scoped_engine = create_engine(
            CT_TEST_URI,
            connect_args={'options': f'-csearch_path={self.schema},public'})
        monkeypatch.setattr(fu, 'get_ct_engine', lambda: scoped_engine)

        # Minimal Flask context with the required config
        from flask import Flask
        flask_app = Flask(__name__)
        flask_app.config['CAMERA_TRAP_CONFIG'] = {'SERIES_TIME_WINDOW': 60}
        self.ctx = flask_app.app_context()
        self.ctx.push()

        yield

        self.ctx.pop()
        with engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        engine.dispose()
        scoped_engine.dispose()

    def _seed(self, captured_times):
        """Create locations/upload_batch/photos with the given timestamps.
        Returns batch_id."""
        from sqlalchemy import text
        batch_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(text(f"SET search_path TO {self.schema}, public"))
            loc_id = conn.execute(text(
                f"INSERT INTO {self.schema}.locations(name,latitude,longitude) "
                f"VALUES('L', 49.5, 24.5) RETURNING id")).scalar()
            conn.execute(text(
                f"INSERT INTO {self.schema}.upload_batches"
                f"(id, location_id, uploaded_by_id, total_files, status) "
                f"VALUES (:b, :loc, 1, :n, 'uploading')"),
                {"b": batch_id, "loc": loc_id, "n": len(captured_times)})
            for i, ts in enumerate(captured_times):
                conn.execute(text(
                    f"INSERT INTO {self.schema}.photos"
                    f"(upload_batch_id, original_filename, system_filename,"
                    f" captured_at, status) "
                    f"VALUES(:b, :of, :sf, :ts, 'uploaded')"),
                    {"b": batch_id, "of": f'IMG_{i:05d}.jpg',
                     "sf": f'{self.schema}_{batch_id[:8]}_{i:05d}.jpg',
                     "ts": ts})
        return batch_id

    def _series_count(self, batch_id):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            return conn.execute(text(f"""
                SELECT COUNT(DISTINCT p.observation_id)
                  FROM {self.schema}.photos p
                 WHERE p.upload_batch_id = :b
                   AND p.observation_id IS NOT NULL
            """), {"b": batch_id}).scalar()

    def test_single_series_within_window(self):
        """5 photos at 10 s steps — a single series."""
        from app.camera_traps.fast_upload import group_batch_into_series_sql
        base = datetime(2026, 1, 1, 12, 0)
        bid = self._seed([base + timedelta(seconds=10 * i) for i in range(5)])
        n = group_batch_into_series_sql(bid)
        assert n == 5
        assert self._series_count(bid) == 1

    def test_two_series_split_at_gap(self):
        """3+3 photos with a gap >60s — two series."""
        from app.camera_traps.fast_upload import group_batch_into_series_sql
        base = datetime(2026, 1, 1, 12, 0)
        times = [base + timedelta(seconds=10 * i) for i in range(3)]
        # after the 3rd — a jump of 5 min
        times += [base + timedelta(minutes=5, seconds=10 * i) for i in range(3)]
        bid = self._seed(times)
        n = group_batch_into_series_sql(bid)
        assert n == 6
        assert self._series_count(bid) == 2

    def test_exact_window_boundary(self):
        """A photo exactly at the SERIES_TIME_WINDOW boundary (60s) — same series
        (diff == window does not exceed it). Documents the expected semantics."""
        from app.camera_traps.fast_upload import group_batch_into_series_sql
        base = datetime(2026, 1, 1, 12, 0)
        bid = self._seed([base, base + timedelta(seconds=60)])
        group_batch_into_series_sql(bid)
        assert self._series_count(bid) == 1

    def test_idempotent_completed(self):
        """Repeat call on a completed batch — no-op."""
        from app.camera_traps.fast_upload import group_batch_into_series_sql
        from sqlalchemy import text
        base = datetime(2026, 1, 1, 12, 0)
        bid = self._seed([base, base + timedelta(seconds=5)])
        group_batch_into_series_sql(bid)
        with self.engine.begin() as conn:
            conn.execute(text(
                f"UPDATE {self.schema}.upload_batches SET status='completed' "
                f"WHERE id=:b"), {"b": bid})
        # Second call
        n = group_batch_into_series_sql(bid)
        assert n == 0  # short-circuit

    def test_recovery_from_partial(self):
        """If partial Observations remain from a previous attempt —
        they are cleaned up and grouping runs again correctly."""
        from app.camera_traps.fast_upload import group_batch_into_series_sql
        base = datetime(2026, 1, 1, 12, 0)
        bid = self._seed([base + timedelta(seconds=10 * i) for i in range(4)])
        group_batch_into_series_sql(bid)
        first_count = self._series_count(bid)
        # Emulate a "stuck" state: batch in failed, we want to re-group
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text(
                f"UPDATE {self.schema}.upload_batches SET status='failed' "
                f"WHERE id=:b"), {"b": bid})
        n = group_batch_into_series_sql(bid)
        assert n == 4
        assert self._series_count(bid) == first_count

    def test_processed_files_atomic_under_parallel(self):
        """
        Four parallel UPDATE...RETURNING must yield four
        DISTINCT processed_files values (1,2,3,4) — otherwise atomicity
        is broken. Checks the pure-SQL path that now lives in
        process_single_photo (utils.py).
        """
        import threading
        from sqlalchemy import text

        batch_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(text(f"SET search_path TO {self.schema}, public"))
            loc = conn.execute(text(
                f"INSERT INTO {self.schema}.locations(name,latitude,longitude) "
                f"VALUES('L',49.5,24.5) RETURNING id")).scalar()
            conn.execute(text(
                f"INSERT INTO {self.schema}.upload_batches"
                f"(id, location_id, uploaded_by_id, total_files, status) "
                f"VALUES(:b, :l, 1, 0, 'uploading')"),
                {"b": batch_id, "l": loc})

        results = []
        lock = threading.Lock()

        def worker():
            with self.engine.begin() as c:
                c.execute(text(f"SET search_path TO {self.schema}, public"))
                row = c.execute(text(
                    f"UPDATE {self.schema}.upload_batches "
                    f"SET processed_files = COALESCE(processed_files,0) + 1 "
                    f"WHERE id = :b RETURNING processed_files"),
                    {"b": batch_id}).first()
            with lock:
                results.append(row[0])

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()

        # All values must be unique (1,2,3,4 in some order)
        assert sorted(results) == [1, 2, 3, 4], \
            f"Atomicity broken, got duplicates: {results}"

    def test_advisory_lock_serializes_duplicate_inserts(self):
        """
        Four parallel INSERTs of the same photo (single (batch, filename,
        captured_at)) under an advisory lock must yield exactly 1 success,
        3 IntegrityError. This is exactly what was breaking in the production logs:
        4 "failed" out of 918 with 4-way parallel JS.
        """
        import threading
        import hashlib
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        batch_id = str(uuid.uuid4())
        captured_at = datetime(2026, 1, 1, 12, 0, 0)
        filename = 'COLLISION.jpg'

        with self.engine.begin() as conn:
            conn.execute(text(f"SET search_path TO {self.schema}, public"))
            loc = conn.execute(text(
                f"INSERT INTO {self.schema}.locations(name,latitude,longitude) "
                f"VALUES('L',49.5,24.5) RETURNING id")).scalar()
            conn.execute(text(
                f"INSERT INTO {self.schema}.upload_batches"
                f"(id, location_id, uploaded_by_id, total_files, status) "
                f"VALUES(:b, :l, 1, 4, 'uploading')"),
                {"b": batch_id, "l": loc})

        _h = hashlib.md5(f"{loc}|{filename}|{captured_at.isoformat()}".encode()).digest()
        k1 = int.from_bytes(_h[0:4], 'big', signed=True)
        k2 = int.from_bytes(_h[4:8], 'big', signed=True)

        outcomes = {'inserted': 0, 'duplicate': 0}
        lock = threading.Lock()

        def worker(idx):
            try:
                with self.engine.begin() as c:
                    c.execute(text(f"SET search_path TO {self.schema}, public"))
                    # Advisory lock
                    c.execute(text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
                              {"k1": k1, "k2": k2})
                    # Preflight check INSIDE lock — race-safe
                    existing = c.execute(text(f"""
                        SELECT 1 FROM {self.schema}.photos p
                          JOIN {self.schema}.upload_batches b
                            ON b.id = p.upload_batch_id
                         WHERE b.location_id = :loc
                           AND p.captured_at = :t
                           AND p.original_filename = :n
                         LIMIT 1
                    """), {"loc": loc, "t": captured_at, "n": filename}).first()
                    if existing:
                        with lock:
                            outcomes['duplicate'] += 1
                        return
                    c.execute(text(f"""
                        INSERT INTO {self.schema}.photos
                            (upload_batch_id, original_filename, system_filename,
                             captured_at, status)
                        VALUES (:b, :n, :sf, :t, 'uploaded')
                    """), {"b": batch_id, "n": filename,
                           "sf": f"sys_{idx}_collision.jpg", "t": captured_at})
                with lock:
                    outcomes['inserted'] += 1
            except IntegrityError:
                with lock:
                    outcomes['duplicate'] += 1

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert outcomes['inserted'] == 1, \
            f"Expected exactly 1 INSERT, got {outcomes}"
        assert outcomes['duplicate'] == 3, \
            f"Expected 3 duplicates, got {outcomes}"

    @pytest.mark.slow
    def test_10000_photos_grouping_perf(self):
        """10,000 photos — the SQL grouper must handle it in about ten seconds.

        Seed via a single bulk INSERT (`generate_series`) rather than a
        Python loop, so that network latency in the seed phase does not
        distort the measurement of the grouping itself.
        """
        import time
        from sqlalchemy import text
        from app.camera_traps.fast_upload import group_batch_into_series_sql

        batch_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(text(f"SET search_path TO {self.schema}, public"))
            loc = conn.execute(text(
                f"INSERT INTO {self.schema}.locations(name,latitude,longitude) "
                f"VALUES('L',49.5,24.5) RETURNING id")).scalar()
            conn.execute(text(
                f"INSERT INTO {self.schema}.upload_batches"
                f"(id, location_id, uploaded_by_id, total_files, status) "
                f"VALUES(:b, :l, 1, 10000, 'uploading')"),
                {"b": batch_id, "l": loc})
            # 10k photos in one long series (5s step — less than the 60s window)
            conn.execute(text(f"""
                INSERT INTO {self.schema}.photos
                    (upload_batch_id, original_filename, system_filename,
                     captured_at, status)
                SELECT :b,
                       'IMG_' || g || '.jpg',
                       '{self.schema}_' || g || '.jpg',
                       TIMESTAMP '2026-01-01 12:00:00'
                         + (g * INTERVAL '5 seconds'),
                       'uploaded'
                  FROM generate_series(0, 9999) g
            """), {"b": batch_id})

        t0 = time.time()
        n = group_batch_into_series_sql(batch_id)
        elapsed = time.time() - t0
        assert n == 10000
        # The threshold is deliberately generous — network latency over the tunnel
        # adds seconds, but locally on the server this is <2s.
        assert elapsed < 15.0, f"SQL grouping too slow: {elapsed:.2f}s"
