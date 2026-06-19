"""
Integration tests for the admin-only location data-validity flag (camera traps).

Covers:
  - set_location_validity (POST)   - admin marks a location invalid / restores it
  - access control                 - non-admin (manager) is redirected (302)
  - not-found                      - 404 when the location does not exist
  - manual_run_analytics           - the "discard invalid locations" checkbox is
                                     forwarded to start_async_analytics (default ON)

Key points:
  - CT uses SQLAlchemy ORM (get_ct_session / close_ct_session), mocked here.
  - CT role_required returns 302 (redirect), not 403.
  - The validity endpoint is admin-only.

Run:
    venv/bin/python -m unittest tests.test_ct_location_validity -v
"""

import os
import unittest
from unittest.mock import patch, MagicMock


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _make_location(id=1, name='Тест ліс', is_valid=True, invalid_note=None):
    loc = MagicMock()
    loc.id = id
    loc.name = name
    loc.is_valid = is_valid
    loc.invalid_note = invalid_note
    return loc


def _make_validity_session(location):
    """Mock ct_session where query(Location).get(id) -> location (or None)."""
    mock_session = MagicMock()
    q = MagicMock()
    q.get.return_value = location
    mock_session.query.return_value = q
    return mock_session


class CtLocationValidityBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock()
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
        self.db = db
        self._seed()
        self.client = self.app.test_client()

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self):
        from app.extensions import db, bcrypt
        from app.models import User, Role

        role_admin = Role(name='admin')
        role_manager = Role(name='manager')
        db.session.add_all([role_admin, role_manager])
        db.session.flush()

        pw = bcrypt.generate_password_hash('testpass').decode('utf-8')

        self.admin = User(username='admin_user', password_hash=pw)
        self.admin.roles.append(role_admin)
        db.session.add(self.admin)

        self.manager = User(username='manager_user', password_hash=pw)
        self.manager.roles.append(role_manager)
        db.session.add(self.manager)

        db.session.commit()

    def _post_validity(self, location_id, payload, user_id, session):
        _login(self.client, user_id)
        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            return self.client.post(
                f'/uk/camera-traps/location/{location_id}/validity',
                json=payload,
                headers={'X-CSRFToken': 'test', 'X-Requested-With': 'XMLHttpRequest'},
            )


class TestSetLocationValidity(CtLocationValidityBase):

    def test_admin_marks_location_invalid(self):
        loc = _make_location(1, is_valid=True)
        session = _make_validity_session(loc)
        resp = self._post_validity(1, {'is_valid': False, 'note': 'погані координати'},
                                   user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertFalse(data['is_valid'])
        self.assertEqual(loc.is_valid, False)
        self.assertEqual(loc.invalid_note, 'погані координати')
        session.commit.assert_called_once()

    def test_admin_restores_location_valid_clears_note(self):
        loc = _make_location(1, is_valid=False, invalid_note='стара причина')
        session = _make_validity_session(loc)
        resp = self._post_validity(1, {'is_valid': True},
                                   user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(loc.is_valid)
        self.assertIsNone(loc.invalid_note)

    def test_manager_is_forbidden_redirect(self):
        loc = _make_location(1)
        session = _make_validity_session(loc)
        resp = self._post_validity(1, {'is_valid': False},
                                   user_id=self.manager.id, session=session)
        # CT role_required redirects (302) rather than 403; the location must be untouched.
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('login', resp.headers.get('Location', ''))
        session.commit.assert_not_called()

    def test_missing_location_returns_404(self):
        session = _make_validity_session(None)
        resp = self._post_validity(999, {'is_valid': False},
                                   user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 404)


class TestRecalcCheckboxForwarding(CtLocationValidityBase):
    """The admin recalc checkbox is forwarded to start_async_analytics (default ON)."""

    URL = '/uk/camera-traps/admin/run-analytics'

    def _run(self, payload):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.analytics_calculator.start_async_analytics',
                   return_value=True) as mock_start:
            self.client.post(self.URL, json=payload,
                             headers={'X-CSRFToken': 'test',
                                      'X-Requested-With': 'XMLHttpRequest'})
            return mock_start

    def test_checkbox_false_is_forwarded(self):
        mock_start = self._run({'exclude_invalid_locations': False})
        mock_start.assert_called_once()
        self.assertIs(mock_start.call_args.kwargs['exclude_invalid_locations'], False)

    def test_absent_defaults_to_true(self):
        mock_start = self._run({})
        mock_start.assert_called_once()
        self.assertIs(mock_start.call_args.kwargs['exclude_invalid_locations'], True)


if __name__ == '__main__':
    unittest.main()
