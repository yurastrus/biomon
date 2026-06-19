"""
Tests for async analytics recalculation (analytics_calculator + routes).

Why this exists: update_analytics_tables() takes ~3 min on production data and,
called synchronously from an HTTP request, exceeded gunicorn --timeout → the worker
was killed → 500 on /admin/run-analytics. Reworked into a background thread with
state in calculation_log and polling, following the cleanup.py / fast_upload.py pattern.

Test layers:
  • UNIT (always) — update_analytics_tables contract (return bool),
    start_async_analytics / _run_analytics_in_thread orchestration (mocks),
    route access and response codes.
  • INTEGRATION (real Postgres, CT_TEST_DATABASE_URI) — compare-and-set guard,
    recover_stuck_analytics, get_analytics_status against a real calculation_log
    (ON CONFLICT / IS DISTINCT FROM — PG-only).

Run:
    venv/Scripts/python -m pytest tests/test_analytics_async.py -v
    CT_TEST_DATABASE_URI=postgresql://... -m integration
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _calc_session(current_count, last_count):
    """
    Mock get_ct_session() for update_analytics_tables:
      • session.query(func.count(...)).filter(...).scalar() → current_count
      • session.query(CalculationLog).filter_by(...).first() → log_entry|None
    Both query() calls return the same q, so we configure both branches.
    """
    log_entry = None
    if last_count is not None:
        log_entry = MagicMock()
        log_entry.last_count = last_count

    q = MagicMock()
    q.filter.return_value = q
    q.scalar.return_value = current_count
    q.filter_by.return_value = q
    q.first.return_value = log_entry

    sess = MagicMock()
    sess.query.return_value = q
    return sess, log_entry


# ═════════════════════════════════════════════════════════════════════════════
# UNIT — update_analytics_tables contract (return True/False)
# ═════════════════════════════════════════════════════════════════════════════

class TestUpdateAnalyticsReturnValue(unittest.TestCase):

    def _run(self, current_count, last_count, force_run,
             monthly=True, yearly=True):
        from app.camera_traps import analytics_calculator as ac
        sess, _ = _calc_session(current_count, last_count)
        with patch.object(ac, 'get_ct_session', return_value=sess), \
             patch.object(ac, 'close_ct_session'), \
             patch.object(ac, '_calculate_monthly_activity',
                          return_value=monthly) as m_month, \
             patch.object(ac, '_calculate_yearly_trends_with_bootstrap',
                          return_value=yearly) as m_year:
            result = ac.update_analytics_tables(force_run=force_run)
        return result, m_month, m_year

    def test_no_changes_returns_true_and_skips_calc(self):
        result, m_month, m_year = self._run(
            current_count=5, last_count=5, force_run=False)
        self.assertTrue(result)
        m_month.assert_not_called()
        m_year.assert_not_called()

    def test_force_run_both_stages_ok_returns_true(self):
        result, m_month, m_year = self._run(
            current_count=5, last_count=5, force_run=True)
        self.assertTrue(result)
        m_month.assert_called_once()
        m_year.assert_called_once()

    def test_changes_detected_runs_calc_returns_true(self):
        result, m_month, m_year = self._run(
            current_count=10, last_count=5, force_run=False)
        self.assertTrue(result)
        m_month.assert_called_once()
        m_year.assert_called_once()

    def test_monthly_failure_returns_false_and_skips_yearly(self):
        result, m_month, m_year = self._run(
            current_count=10, last_count=5, force_run=True, monthly=False)
        self.assertFalse(result)
        m_month.assert_called_once()
        m_year.assert_not_called()

    def test_yearly_failure_returns_false(self):
        result, m_month, m_year = self._run(
            current_count=10, last_count=5, force_run=True, yearly=False)
        self.assertFalse(result)
        m_year.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# Base class with app context + seeded users (like CleanupRouteBase)
# ═════════════════════════════════════════════════════════════════════════════

class AnalyticsBase(unittest.TestCase):

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
        from app.models import User, Role
        r_admin = Role(name='admin')
        r_manager = Role(name='manager')
        db.session.add_all([r_admin, r_manager])
        db.session.flush()
        pw = bcrypt.generate_password_hash('test').decode('utf-8')
        self.admin = User(username='a', password_hash=pw)
        self.admin.roles.append(r_admin)
        self.manager = User(username='m', password_hash=pw)
        self.manager.roles.append(r_manager)
        db.session.add_all([self.admin, self.manager])
        db.session.commit()


# ═════════════════════════════════════════════════════════════════════════════
# UNIT — start_async_analytics / _run_analytics_in_thread orchestration
# ═════════════════════════════════════════════════════════════════════════════

class TestAsyncOrchestration(AnalyticsBase):

    def test_start_returns_false_when_already_running(self):
        from app.camera_traps import analytics_calculator as ac
        with patch.object(ac, 'try_start_analytics_run', return_value=False), \
             patch.object(ac, 'threading') as m_threading:
            started = ac.start_async_analytics(triggered_by=1)
        self.assertFalse(started)
        m_threading.Thread.assert_not_called()

    def test_start_spawns_thread_when_acquired(self):
        from app.camera_traps import analytics_calculator as ac
        with patch.object(ac, 'try_start_analytics_run', return_value=True), \
             patch.object(ac, 'threading') as m_threading:
            started = ac.start_async_analytics(triggered_by=1)
        self.assertTrue(started)
        m_threading.Thread.assert_called_once()
        m_threading.Thread.return_value.start.assert_called_once()

    def test_thread_marks_completed_on_success(self):
        from app.camera_traps import analytics_calculator as ac
        with patch.object(ac, 'update_analytics_tables', return_value=True), \
             patch.object(ac, '_finish_analytics_run') as m_finish:
            ac._run_analytics_in_thread(self.app, triggered_by=1)
        m_finish.assert_called_once()
        self.assertEqual(m_finish.call_args.args[0], 'completed')

    def test_thread_marks_failed_when_calc_returns_false(self):
        from app.camera_traps import analytics_calculator as ac
        with patch.object(ac, 'update_analytics_tables', return_value=False), \
             patch.object(ac, '_finish_analytics_run') as m_finish:
            ac._run_analytics_in_thread(self.app, triggered_by=1)
        m_finish.assert_called_once()
        self.assertEqual(m_finish.call_args.args[0], 'failed')

    def test_thread_marks_failed_on_exception(self):
        from app.camera_traps import analytics_calculator as ac
        with patch.object(ac, 'update_analytics_tables',
                          side_effect=RuntimeError('boom')), \
             patch.object(ac, '_finish_analytics_run') as m_finish:
            ac._run_analytics_in_thread(self.app, triggered_by=1)
        m_finish.assert_called_once()
        self.assertEqual(m_finish.call_args.args[0], 'failed')


# ═════════════════════════════════════════════════════════════════════════════
# UNIT — routes: access + response codes
# ═════════════════════════════════════════════════════════════════════════════

class TestAnalyticsRoutes(AnalyticsBase):

    RUN_URL = '/uk/camera-traps/admin/run-analytics'
    STATUS_URL = '/uk/camera-traps/admin/analytics/status'

    def test_run_ajax_started_returns_202(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.analytics_calculator.start_async_analytics',
                   return_value=True) as m:
            resp = self.client.post(
                self.RUN_URL, json={},
                headers={'Accept': 'application/json',
                         'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.get_json()['status'], 'running')
        m.assert_called_once()

    def test_run_ajax_already_running_returns_409(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.analytics_calculator.start_async_analytics',
                   return_value=False):
            resp = self.client.post(
                self.RUN_URL, json={},
                headers={'Accept': 'application/json',
                         'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 409)

    def test_run_form_redirects(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.analytics_calculator.start_async_analytics',
                   return_value=True):
            resp = self.client.post(self.RUN_URL)
        self.assertEqual(resp.status_code, 302)

    def test_run_manager_forbidden(self):
        _login(self.client, self.manager.id)
        resp = self.client.post(self.RUN_URL, json={},
                                headers={'Accept': 'application/json'})
        self.assertIn(resp.status_code, (302, 403))

    def test_run_anonymous_redirects(self):
        resp = self.client.post(self.RUN_URL)
        self.assertEqual(resp.status_code, 302)

    def test_status_admin_returns_json(self):
        _login(self.client, self.admin.id)
        payload = {'status': 'idle', 'started_at': None,
                   'last_calculated_at': None, 'last_count': 0,
                   'error_message': None}
        with patch('app.camera_traps.analytics_calculator.get_analytics_status',
                   return_value=payload):
            resp = self.client.get(self.STATUS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['status'], 'idle')

    def test_status_manager_forbidden(self):
        _login(self.client, self.manager.id)
        resp = self.client.get(self.STATUS_URL)
        self.assertIn(resp.status_code, (302, 403))


# ═════════════════════════════════════════════════════════════════════════════
# INTEGRATION — guard / recovery / status against real Postgres
# ═════════════════════════════════════════════════════════════════════════════

CT_TEST_URI = os.environ.get('CT_TEST_DATABASE_URI', '')
INTEGRATION_AVAILABLE = CT_TEST_URI and not CT_TEST_URI.startswith('sqlite')


@pytest.mark.integration
@pytest.mark.skipif(not INTEGRATION_AVAILABLE,
                    reason="CT_TEST_DATABASE_URI not set (Postgres required)")
class TestAnalyticsGuardIntegration:
    """
    Exercises PG-specific logic (ON CONFLICT, IS DISTINCT FROM) against a real
    calculation_log in a temporary schema.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        from sqlalchemy import create_engine, text
        from app.camera_traps import analytics_calculator as ac

        self.engine = create_engine(CT_TEST_URI)
        self.schema = f"test_analytics_{uuid.uuid4().hex[:8]}"

        with self.engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {self.schema}"))
            conn.execute(text(f"""
                CREATE TABLE {self.schema}.calculation_log (
                    id SERIAL PRIMARY KEY,
                    source_name VARCHAR(100) UNIQUE NOT NULL,
                    last_count INTEGER NOT NULL DEFAULT 0,
                    last_calculated_at TIMESTAMP,
                    status VARCHAR(20) NOT NULL DEFAULT 'idle',
                    started_at TIMESTAMP,
                    error_message TEXT
                )"""))

        scoped_engine = create_engine(
            CT_TEST_URI,
            connect_args={'options': f'-csearch_path={self.schema},public'})
        monkeypatch.setattr(ac, 'get_ct_engine', lambda: scoped_engine)

        from flask import Flask
        flask_app = Flask(__name__)
        self.ctx = flask_app.app_context()
        self.ctx.push()

        self._engine_scoped = scoped_engine
        yield

        self.ctx.pop()
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))
        self.engine.dispose()
        scoped_engine.dispose()

    def _status_row(self):
        from sqlalchemy import text
        with self._engine_scoped.begin() as c:
            return c.execute(text(
                "SELECT status, started_at, last_calculated_at, error_message "
                "FROM calculation_log WHERE source_name='completed_observations'"
            )).first()

    def test_second_concurrent_start_is_rejected(self):
        from app.camera_traps.analytics_calculator import try_start_analytics_run
        first = try_start_analytics_run(triggered_by=1)
        second = try_start_analytics_run(triggered_by=2)
        assert first is True
        assert second is False, "Other run already in progress must be rejected"

    def test_can_restart_after_finish(self):
        from app.camera_traps.analytics_calculator import (
            try_start_analytics_run, _finish_analytics_run)
        assert try_start_analytics_run(triggered_by=1) is True
        _finish_analytics_run('completed')
        # after finishing, a new run is allowed
        assert try_start_analytics_run(triggered_by=1) is True

    def test_stuck_running_can_be_reacquired(self):
        from sqlalchemy import text
        from app.camera_traps.analytics_calculator import (
            try_start_analytics_run, ANALYTICS_STUCK_MINUTES)
        # a 'running' row with an old started_at — stuck
        old = datetime.utcnow() - timedelta(minutes=ANALYTICS_STUCK_MINUTES + 5)
        with self._engine_scoped.begin() as c:
            c.execute(text("""
                INSERT INTO calculation_log (source_name, last_count, status, started_at)
                VALUES ('completed_observations', 0, 'running', :ts)
            """), {"ts": old})
        assert try_start_analytics_run(triggered_by=1) is True

    def test_recover_stuck_analytics_marks_failed(self):
        from sqlalchemy import text
        from app.camera_traps.analytics_calculator import (
            recover_stuck_analytics, ANALYTICS_STUCK_MINUTES)
        old = datetime.utcnow() - timedelta(minutes=ANALYTICS_STUCK_MINUTES + 5)
        with self._engine_scoped.begin() as c:
            c.execute(text("""
                INSERT INTO calculation_log (source_name, last_count, status, started_at)
                VALUES ('completed_observations', 0, 'running', :ts)
            """), {"ts": old})
        n = recover_stuck_analytics()
        assert n == 1
        assert self._status_row().status == 'failed'

    def test_recover_leaves_fresh_running_untouched(self):
        from sqlalchemy import text
        from app.camera_traps.analytics_calculator import recover_stuck_analytics
        with self._engine_scoped.begin() as c:
            c.execute(text("""
                INSERT INTO calculation_log (source_name, last_count, status, started_at)
                VALUES ('completed_observations', 0, 'running', NOW())
            """))
        n = recover_stuck_analytics()
        assert n == 0
        assert self._status_row().status == 'running'

    def test_get_status_shape(self):
        from app.camera_traps.analytics_calculator import get_analytics_status
        data = get_analytics_status()
        assert set(data.keys()) >= {
            'status', 'started_at', 'last_calculated_at',
            'last_count', 'error_message'}
        # _ensure_log_row created a row with status 'idle'
        assert data['status'] == 'idle'

    def test_finish_failed_records_error_message(self):
        from app.camera_traps.analytics_calculator import (
            try_start_analytics_run, _finish_analytics_run)
        try_start_analytics_run(triggered_by=1)
        _finish_analytics_run('failed', 'something broke')
        row = self._status_row()
        assert row.status == 'failed'
        assert row.error_message == 'something broke'
        # 'failed' does NOT update the last-success time
        assert row.last_calculated_at is None

    def test_finish_completed_sets_last_calculated_at(self):
        from app.camera_traps.analytics_calculator import (
            try_start_analytics_run, _finish_analytics_run)
        try_start_analytics_run(triggered_by=1)
        # before finishing there is no success time yet
        assert self._status_row().last_calculated_at is None
        _finish_analytics_run('completed')
        row = self._status_row()
        assert row.status == 'completed'
        assert row.error_message is None
        # the "Last successful recalculation" badge gets a real timestamp
        assert row.last_calculated_at is not None
