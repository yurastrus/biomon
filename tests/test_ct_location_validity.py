"""
Integration tests for the location data-validity flag (camera traps).

Covers:
  - set_location_validity (POST)   - mark a location invalid / restore it
  - access control                 - manager+ scoped to their institutions:
                                     admin → any location; manager → only own
                                     institutions (403 otherwise); viewer → 302
  - not-found                      - 404 when the location does not exist
  - manual_run_analytics           - the "discard invalid locations" checkbox is
                                     forwarded to start_async_analytics (default ON)

Key points:
  - CT uses SQLAlchemy ORM (get_ct_session / close_ct_session), mocked here.
  - CT role_required returns 302 (redirect) for an insufficient role.
  - The validity endpoint is manager+; managers may only toggle locations
    belonging to one of their institutions.

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


def _make_validity_session(location, has_access=True):
    """Mock ct_session: query(Location).get(id) -> location (or None);
    execute().fetchone() -> (1,) if has_access (institution check), else None."""
    mock_session = MagicMock()
    q = MagicMock()
    q.get.return_value = location
    mock_session.query.return_value = q
    access_result = MagicMock()
    access_result.fetchone.return_value = (1,) if has_access else None
    mock_session.execute.return_value = access_result
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
        from app.models import User, Role, Institution, UserInstitution

        role_admin = Role(name='admin')
        role_manager = Role(name='manager')
        role_viewer = Role(name='viewer')
        db.session.add_all([role_admin, role_manager, role_viewer])
        db.session.flush()

        inst = Institution(name_uk='Заповідник А', name_en='Reserve A', code='res_a')
        db.session.add(inst)
        db.session.flush()

        pw = bcrypt.generate_password_hash('testpass').decode('utf-8')

        self.admin = User(username='admin_user', password_hash=pw)
        self.admin.roles.append(role_admin)
        db.session.add(self.admin)

        # Manager with an institution → may toggle locations in that institution.
        self.manager = User(username='manager_user', password_hash=pw)
        self.manager.roles.append(role_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=inst.id, can_export=False)
        )
        db.session.add(self.manager)

        # Manager with no institutions → no location access.
        self.manager_no_inst = User(username='manager_no_inst', password_hash=pw)
        self.manager_no_inst.roles.append(role_manager)
        db.session.add(self.manager_no_inst)

        self.viewer = User(username='viewer_user', password_hash=pw)
        self.viewer.roles.append(role_viewer)
        db.session.add(self.viewer)

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

    def test_manager_with_access_marks_invalid(self):
        """Manager toggling a location in their own institution succeeds."""
        loc = _make_location(1, is_valid=True)
        session = _make_validity_session(loc, has_access=True)
        resp = self._post_validity(1, {'is_valid': False, 'note': 'погані координати'},
                                   user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])
        self.assertFalse(loc.is_valid)
        session.commit.assert_called_once()

    def test_manager_without_access_forbidden_403(self):
        """Location outside the manager's institutions → 403, untouched."""
        loc = _make_location(1)
        session = _make_validity_session(loc, has_access=False)
        resp = self._post_validity(1, {'is_valid': False},
                                   user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 403)
        session.commit.assert_not_called()

    def test_manager_without_institution_forbidden_403(self):
        loc = _make_location(1)
        session = _make_validity_session(loc, has_access=False)
        resp = self._post_validity(1, {'is_valid': False},
                                   user_id=self.manager_no_inst.id, session=session)
        self.assertEqual(resp.status_code, 403)
        session.commit.assert_not_called()

    def test_viewer_is_forbidden_redirect(self):
        """Viewer < manager → role_required redirects (302), untouched."""
        loc = _make_location(1)
        session = _make_validity_session(loc)
        resp = self._post_validity(1, {'is_valid': False},
                                   user_id=self.viewer.id, session=session)
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
