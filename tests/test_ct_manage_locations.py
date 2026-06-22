"""
Integration tests for camera trap location management.

Covers:
  - manage_locations (GET)            - access, content, filtering by institution
  - service_log (GET)                 - redirect to manage_locations
  - api_get_locations_with_status     - access (manager+), filtering, JSON fields
  - api_get_service_history           - access (manager+), institution check
  - api_create_service_visit          - access, validation, institution check
  - api_update_service_visit          - access, ownership + institution guard
  - api_create_location_admin         - manager+ with institution, admin unrestricted
  - update_location                   - manager+ with institution, admin unrestricted

Key points:
  - CT uses SQLAlchemy ORM (get_ct_session / close_ct_session)
  - CT role_required returns 302 (redirect), not 403
  - Institution filtering: a manager sees only their own institutions

Run:
    venv/Scripts/python -m unittest tests.test_ct_manage_locations -v
"""

import os
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock


# ── helpers ────────────────────────────────────────────────────────────────

def _login(client, user_id):
    """Sets up a Flask-Login session without an HTTP request."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _make_location(id=1, name='Тест ліс', lat=49.85, lon=23.65,
                   description='Короткий опис', biotope_ids=()):
    """
    Mock Location object.
    loc.stats = None avoids TypeError (MagicMock > int) in api_get_locations_with_status.
    """
    loc = MagicMock()
    loc.id = id
    loc.name = name
    loc.latitude = lat
    loc.longitude = lon
    loc.description = description
    loc.biotopes = [MagicMock(id=bid) for bid in biotope_ids]
    loc.stats = None
    # Data-validity flag (model columns); JSON-serialisable values so the
    # locations|tojson block in manage_locations renders.
    loc.is_valid = True
    loc.invalid_note = None
    return loc


def _make_visit(id=1, location_id=1, user_id=1, visit_purpose_id=1,
                battery_type_id=None, is_camera_operational=None,
                sd_card_changed=False, photos_on_card=None, comments=None,
                visit_dt=None):
    """Mock ServiceVisit object."""
    v = MagicMock()
    v.id = id
    v.location_id = location_id
    v.user_id = user_id
    v.visit_purpose_id = visit_purpose_id
    v.battery_type_id = battery_type_id
    v.is_camera_operational = is_camera_operational
    v.sd_card_changed = sd_card_changed
    v.photos_on_card = photos_on_card
    v.comments = comments
    v.visit_datetime = visit_dt or datetime(2026, 4, 10, 12, 0)
    v.visit_purpose.get_name.return_value = 'Планова перевірка'
    v.battery_type = None
    return v


def _make_manage_session(locations=(), biotopes=(), battery_types=(), visit_purposes=()):
    """
    Mock ct_session for manage_locations:
    4 sequential query() calls for Location, Biotope, BatteryType, VisitPurpose.
    Supports both Location patterns:
      - admin:   .query(Location).order_by(...).all()
      - manager: .query(Location).join(...).filter(...).order_by(...).distinct().all()
    """
    mock_session = MagicMock()
    results = [
        list(locations),
        list(biotopes),
        list(battery_types),
        list(visit_purposes),
    ]
    call_idx = [0]

    def query_side_effect(_model):
        q = MagicMock()
        idx = call_idx[0]
        call_idx[0] += 1
        lst = results[idx] if idx < len(results) else []
        # Support both chains
        q.order_by.return_value.all.return_value = lst
        q.join.return_value.filter.return_value.order_by.return_value.distinct.return_value.all.return_value = lst
        return q

    mock_session.query.side_effect = query_side_effect
    return mock_session


def _make_status_session(locations=()):
    """
    Mock ct_session for api_get_locations_with_status.
    Supports both location-fetching chains (admin/manager).
    For each location: the ServiceVisit query returns None (no visits).
    """
    mock_session = MagicMock()
    call_idx = [0]

    def query_side_effect(model):
        q = MagicMock()
        if call_idx[0] == 0:
            call_idx[0] += 1
            q.all.return_value = list(locations)
            q.join.return_value.filter.return_value.distinct.return_value.all.return_value = list(locations)
        else:
            q.filter.return_value.order_by.return_value.first.return_value = None
        return q

    mock_session.query.side_effect = query_side_effect
    return mock_session


def _make_history_session(visits=(), has_access=True):
    """
    Mock ct_session for api_get_service_history.
    has_access: whether execute() returns (1,) for the institution check.
    """
    mock_session = MagicMock()

    # execute() for the institution access check
    access_result = MagicMock()
    access_result.fetchone.return_value = (1,) if has_access else None
    mock_session.execute.return_value = access_result

    # query(ServiceVisit).filter(...).order_by(...).limit(...).all()
    q = MagicMock()
    q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = list(visits)
    mock_session.query.return_value = q

    return mock_session


def _make_create_visit_session(has_access=True):
    """
    Mock ct_session for api_create_service_visit.
    has_access: whether the institution check passes.
    """
    mock_session = MagicMock()
    access_result = MagicMock()
    access_result.fetchone.return_value = (1,) if has_access else None
    mock_session.execute.return_value = access_result
    return mock_session


def _make_update_visit_session(visit_mock, has_access=True):
    """
    Mock ct_session for api_update_service_visit:
    .query(ServiceVisit).get(visit_id) -> visit_mock or None.
    execute() -> institution check.
    """
    mock_session = MagicMock()
    mock_session.query.return_value.get.return_value = visit_mock
    access_result = MagicMock()
    access_result.fetchone.return_value = (1,) if has_access else None
    mock_session.execute.return_value = access_result
    return mock_session


def _make_create_location_session():
    """Mock ct_session for api_create_location_admin."""
    mock_session = MagicMock()
    mock_session.flush.side_effect = lambda: None
    mock_session.query.return_value.filter.return_value.all.return_value = []
    return mock_session


def _make_update_location_session(location=None, has_access=True):
    """Mock ct_session for update_location."""
    mock_session = MagicMock()
    access_result = MagicMock()
    access_result.fetchone.return_value = (1,) if has_access else None
    mock_session.execute.return_value = access_result

    if location is None:
        location = _make_location(1, 'Стара назва')
    q = MagicMock()
    q.get.return_value = location
    q.filter.return_value.all.return_value = []  # for Biotope
    mock_session.query.return_value = q
    return mock_session


# ── base test class ────────────────────────────────────────────────────────

class CtManageLocationsBase(unittest.TestCase):

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

        self.role_admin   = Role(name='admin')
        self.role_manager = Role(name='manager')
        self.role_viewer  = Role(name='viewer')
        db.session.add_all([self.role_admin, self.role_manager, self.role_viewer])
        db.session.flush()

        self.inst_a = Institution(name_uk='Заповідник А', name_en='Reserve A', code='res_a')
        self.inst_b = Institution(name_uk='Заповідник Б', name_en='Reserve B', code='res_b')
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('testpass').decode('utf-8')

        self.admin = User(username='admin_user', password_hash=pw)
        self.admin.roles.append(self.role_admin)
        db.session.add(self.admin)

        # Manager with institution A
        self.manager = User(username='manager_user', password_hash=pw)
        self.manager.roles.append(self.role_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.manager)

        # Second manager with institution B
        self.manager2 = User(username='manager_user2', password_hash=pw)
        self.manager2.roles.append(self.role_manager)
        self.manager2.institution_links.append(
            UserInstitution(institution_id=self.inst_b.id, can_export=False)
        )
        db.session.add(self.manager2)

        # Manager without institutions
        self.manager_no_inst = User(username='manager_no_inst', password_hash=pw)
        self.manager_no_inst.roles.append(self.role_manager)
        db.session.add(self.manager_no_inst)

        self.viewer = User(username='viewer_user', password_hash=pw)
        self.viewer.roles.append(self.role_viewer)
        db.session.add(self.viewer)

        db.session.commit()

    # ── helpers ───────────────────────────────────────────────────────────

    def _get(self, url, user_id=None, session=None):
        if user_id:
            _login(self.client, user_id)
        if session is not None:
            with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
                 patch('app.camera_traps.routes.close_ct_session'):
                return self.client.get(url)
        return self.client.get(url)

    def _post(self, url, payload, user_id=None, session=None):
        if user_id:
            _login(self.client, user_id)
        if session is not None:
            with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
                 patch('app.camera_traps.routes.close_ct_session'):
                return self.client.post(url, json=payload,
                                        headers={'X-CSRFToken': 'test'})
        return self.client.post(url, json=payload,
                                headers={'X-CSRFToken': 'test'})


# ════════════════════════════════════════════════════════════════════════════
# 1. MANAGE LOCATIONS — ACCESS
# ════════════════════════════════════════════════════════════════════════════

class TestManageLocationsAccess(CtManageLocationsBase):
    """
    GET /camera-traps/manage-locations — role check.
    CT role_required returns 302 for insufficient permissions (not 403).
    """

    URL = '/uk/camera-traps/manage-locations'

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.headers.get('Location', ''))

    def test_viewer_is_redirected_to_dashboard(self):
        resp = self._get(self.URL, user_id=self.viewer.id,
                         session=_make_manage_session())
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('login', resp.headers.get('Location', ''))

    def test_manager_with_institution_can_access(self):
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session())
        self.assertEqual(resp.status_code, 200)

    def test_manager_without_institution_can_access(self):
        """A manager without institutions sees an empty list but can access the page."""
        resp = self._get(self.URL, user_id=self.manager_no_inst.id,
                         session=_make_manage_session())
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_access(self):
        resp = self._get(self.URL, user_id=self.admin.id,
                         session=_make_manage_session())
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════════════════════
# 2. MANAGE LOCATIONS — CONTENT
# ════════════════════════════════════════════════════════════════════════════

class TestManageLocationsContent(CtManageLocationsBase):
    """HTML: locations, buttons, edit permissions."""

    URL = '/uk/camera-traps/manage-locations'

    def test_location_names_appear_in_html(self):
        locs = [
            _make_location(1, 'Дубовий гай', biotope_ids=[1]),
            _make_location(2, 'Болотний масив', description=''),
        ]
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session(locations=locs))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Дубовий гай'.encode(), resp.data)
        self.assertIn('Болотний масив'.encode(), resp.data)

    def test_locations_json_embedded_in_page(self):
        locs = [_make_location(7, 'Серпневий бір', lat=49.9, lon=23.7, biotope_ids=[2])]
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session(locations=locs))
        self.assertIn('Серпневий бір'.encode(), resp.data)

    def test_new_location_button_shown_for_manager_with_institution(self):
        """A manager with an institution can edit -> button is visible."""
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session())
        self.assertIn(b'new-location-btn', resp.data)

    def test_new_location_button_shown_for_admin(self):
        resp = self._get(self.URL, user_id=self.admin.id,
                         session=_make_manage_session())
        self.assertIn(b'new-location-btn', resp.data)

    def test_new_location_button_hidden_for_manager_without_institution(self):
        """Manager without institutions - can_edit=False -> button is hidden."""
        resp = self._get(self.URL, user_id=self.manager_no_inst.id,
                         session=_make_manage_session())
        self.assertNotIn(b'new-location-btn', resp.data)

    def test_edit_form_rendered_for_manager_with_institution(self):
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session())
        self.assertIn(b'edit-form', resp.data)

    def test_institution_dropdown_present_in_create_form(self):
        """The create form includes an institution selector."""
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session())
        self.assertIn(b'institution-select', resp.data)

    def test_invalid_legend_and_marker_rendered(self):
        """Невалідні локації мають окрему малинову позначку + запис у легенді."""
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session())
        self.assertEqual(resp.status_code, 200)
        # Малиновий колір легенди (обидва режими карти).
        self.assertIn(b'#c2185b', resp.data)
        # Підпис легенди.
        self.assertIn('Невалідні'.encode(), resp.data)
        # Окрема SVG-іконка для невалідних маркерів.
        self.assertIn(b'invalidIcon', resp.data)
        self.assertIn(b'marker-invalid-icon', resp.data)

    def test_invalid_location_marked_in_list(self):
        """Невалідна локація отримує клас loc-invalid і бейдж у списку."""
        loc = _make_location(3, 'Стара точка', biotope_ids=[1])
        loc.is_valid = False
        resp = self._get(self.URL, user_id=self.manager.id,
                         session=_make_manage_session(locations=[loc]))
        self.assertIn(b'loc-invalid', resp.data)


# ════════════════════════════════════════════════════════════════════════════
# 3. SERVICE LOG REDIRECT
# ════════════════════════════════════════════════════════════════════════════

class TestServiceLogRedirect(CtManageLocationsBase):
    """/service-log → /manage-locations for all authenticated manager+ users."""

    URL = '/uk/camera-traps/service-log'

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.headers.get('Location', ''))

    def test_viewer_is_redirected_to_dashboard(self):
        _login(self.client, self.viewer.id)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('login', resp.headers.get('Location', ''))

    def test_manager_is_redirected_to_manage_locations(self):
        _login(self.client, self.manager.id)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('manage-locations', resp.headers['Location'])

    def test_admin_is_redirected_to_manage_locations(self):
        _login(self.client, self.admin.id)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('manage-locations', resp.headers['Location'])


# ════════════════════════════════════════════════════════════════════════════
# 4. API LOCATIONS-WITH-STATUS — ACCESS AND RESPONSE
# ════════════════════════════════════════════════════════════════════════════

class TestCtLocationsWithStatus(CtManageLocationsBase):
    """GET /camera-traps/api/locations-with-status — now requires manager+."""

    URL = '/uk/camera-traps/api/locations-with-status'

    def test_anonymous_is_redirected(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected(self):
        """Now @role_required('manager') — viewer gets a redirect."""
        session = _make_status_session()
        resp = self._get(self.URL, user_id=self.viewer.id, session=session)
        self.assertEqual(resp.status_code, 302)

    def test_manager_gets_json_list(self):
        locs = [_make_location(1, 'Озерна галявина', lat=49.8, lon=23.6)]
        session = _make_status_session(locations=locs)
        resp = self._get(self.URL, user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    def test_manager_without_institution_returns_empty_list(self):
        """Manager without institutions → immediately returns []."""
        resp = self._get(self.URL, user_id=self.manager_no_inst.id,
                         session=MagicMock())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_empty_locations_returns_empty_list(self):
        session = _make_status_session(locations=[])
        resp = self._get(self.URL, user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_response_item_has_required_fields(self):
        locs = [_make_location(55, 'Поле тополь', lat=49.7, lon=23.5)]
        session = _make_status_session(locations=locs)
        resp = self._get(self.URL, user_id=self.manager.id, session=session)
        item = resp.get_json()[0]
        for key in ('id', 'name', 'latitude', 'longitude', 'status',
                    'last_visit_date', 'days_since_visit', 'status_reason'):
            self.assertIn(key, item, f"Missing key: {key}")

    def test_location_without_visits_has_unknown_status(self):
        locs = [_make_location(10, 'Молодий сосняк')]
        session = _make_status_session(locations=locs)
        resp = self._get(self.URL, user_id=self.manager.id, session=session)
        item = resp.get_json()[0]
        self.assertEqual(item['status'], 'unknown')
        self.assertEqual(item['last_visit_date'], '---')

    def test_status_ok_for_recent_visit(self):
        """Last visit < 180 days ago → status 'ok'."""
        locs = [_make_location(1, 'Свіжа локація')]
        session = MagicMock()
        call_idx = [0]
        recent_visit = _make_visit(visit_purpose_id=1, visit_dt=datetime.now())

        def query_side_effect(model):
            q = MagicMock()
            if call_idx[0] == 0:
                call_idx[0] += 1
                q.all.return_value = list(locs)
                q.join.return_value.filter.return_value.distinct.return_value.all.return_value = list(locs)
            else:
                q.filter.return_value.order_by.return_value.first.return_value = recent_visit
            return q

        session.query.side_effect = query_side_effect

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            _login(self.client, self.manager.id)
            resp = self.client.get(self.URL)

        self.assertEqual(resp.get_json()[0]['status'], 'ok')


# ════════════════════════════════════════════════════════════════════════════
# 5. API SERVICE HISTORY — ACCESS AND STRUCTURE
# ════════════════════════════════════════════════════════════════════════════

class TestCtServiceHistoryAccess(CtManageLocationsBase):
    """GET /camera-traps/api/location/<id>/service-history — now manager+."""

    def _url(self, location_id=1):
        return f'/uk/camera-traps/api/location/{location_id}/service-history'

    def test_anonymous_is_redirected(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected(self):
        """Now @role_required('manager') — viewer gets a redirect."""
        session = _make_history_session()
        resp = self._get(self._url(), user_id=self.viewer.id, session=session)
        self.assertEqual(resp.status_code, 302)

    def test_manager_with_access_gets_200(self):
        session = _make_history_session(has_access=True)
        resp = self._get(self._url(), user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_manager_without_access_to_location_gets_403(self):
        """A manager without access to this location → 403."""
        session = _make_history_session(has_access=False)
        resp = self._get(self._url(location_id=999), user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_manager_without_institution_gets_403(self):
        session = _make_history_session()
        resp = self._get(self._url(), user_id=self.manager_no_inst.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_admin_bypasses_institution_check(self):
        """Admin skips the institution check — execute() is not called."""
        session = _make_history_session()
        resp = self._get(self._url(), user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        session.execute.assert_not_called()

    def test_empty_history_returns_empty_list(self):
        session = _make_history_session(visits=[], has_access=True)
        resp = self._get(self._url(), user_id=self.manager.id, session=session)
        self.assertEqual(resp.get_json(), [])

    def test_response_contains_visit_fields(self):
        visit = _make_visit(id=5, location_id=1, user_id=self.manager.id)
        session = _make_history_session(visits=[visit], has_access=True)

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            _login(self.client, self.manager.id)
            resp = self.client.get(self._url())

        item = resp.get_json()[0]
        for key in ('id', 'visit_datetime', 'visit_datetime_raw', 'purpose',
                    'visit_purpose_id', 'user', 'is_operational', 'battery_info',
                    'battery_type_id', 'sd_card_changed', 'photos_on_card',
                    'comments', 'is_own'):
            self.assertIn(key, item, f"Missing key: {key}")

    def test_is_own_true_for_owner(self):
        visit = _make_visit(id=1, user_id=self.manager.id)
        session = _make_history_session(visits=[visit], has_access=True)

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            _login(self.client, self.manager.id)
            resp = self.client.get(self._url())

        self.assertTrue(resp.get_json()[0]['is_own'])

    def test_is_own_false_for_other_user(self):
        visit = _make_visit(id=1, user_id=self.admin.id)
        session = _make_history_session(visits=[visit], has_access=True)

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            _login(self.client, self.manager.id)
            resp = self.client.get(self._url())

        self.assertFalse(resp.get_json()[0]['is_own'])

    def test_visit_datetime_raw_format(self):
        visit = _make_visit(id=1, user_id=self.manager.id,
                            visit_dt=datetime(2026, 3, 15, 9, 30))
        session = _make_history_session(visits=[visit], has_access=True)

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            _login(self.client, self.manager.id)
            resp = self.client.get(self._url())

        self.assertEqual(resp.get_json()[0]['visit_datetime_raw'], '2026-03-15T09:30')


# ════════════════════════════════════════════════════════════════════════════
# 6. API CREATE SERVICE VISIT — ACCESS, INSTITUTION, VALIDATION
# ════════════════════════════════════════════════════════════════════════════

class TestCtCreateServiceVisit(CtManageLocationsBase):
    """POST /camera-traps/api/service-log/create"""

    URL = '/uk/camera-traps/api/service-log/create'

    def _valid_payload(self, location_id=1):
        return {
            'location_id':           str(location_id),
            'visit_datetime':        '2026-04-10T10:00',
            'visit_purpose_id':      '1',
            'battery_type_id':       '',
            'is_camera_operational': '',
            'sd_card_changed':       False,
            'photos_on_card':        '',
            'comments':              '',
        }

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected_to_dashboard(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_manager_with_access_creates_successfully(self):
        session = _make_create_visit_session(has_access=True)
        resp = self._post(self.URL, self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.get_json()['success'])

    def test_manager_without_location_access_gets_403(self):
        """Manager without access to the location → 403."""
        session = _make_create_visit_session(has_access=False)
        resp = self._post(self.URL, self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_manager_without_institution_gets_403(self):
        session = _make_create_visit_session()
        resp = self._post(self.URL, self._valid_payload(),
                          user_id=self.manager_no_inst.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_admin_creates_without_institution_check(self):
        """Admin skips the institution check."""
        session = _make_create_visit_session()
        resp = self._post(self.URL, self._valid_payload(),
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 201)
        session.execute.assert_not_called()

    def test_missing_location_id_returns_400(self):
        _login(self.client, self.manager.id)
        payload = self._valid_payload()
        del payload['location_id']
        resp = self.client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_missing_visit_datetime_returns_400(self):
        _login(self.client, self.manager.id)
        payload = self._valid_payload()
        del payload['visit_datetime']
        resp = self.client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_missing_visit_purpose_returns_400(self):
        _login(self.client, self.manager.id)
        payload = self._valid_payload()
        del payload['visit_purpose_id']
        resp = self.client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_datetime_format_returns_400(self):
        session = _make_create_visit_session(has_access=True)
        payload = self._valid_payload()
        payload['visit_datetime'] = 'not-a-date'
        resp = self._post(self.URL, payload,
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 400)

    def test_sd_card_changed_true_accepted(self):
        session = _make_create_visit_session(has_access=True)
        payload = self._valid_payload()
        payload['sd_card_changed'] = True
        payload['photos_on_card'] = '350'
        resp = self._post(self.URL, payload,
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 201)

    def test_session_commit_called_on_success(self):
        session = _make_create_visit_session(has_access=True)
        self._post(self.URL, self._valid_payload(),
                   user_id=self.manager.id, session=session)
        session.commit.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# 7. API UPDATE SERVICE VISIT — OWNERSHIP + INSTITUTION
# ════════════════════════════════════════════════════════════════════════════

class TestCtUpdateServiceVisit(CtManageLocationsBase):
    """POST /camera-traps/api/service-visit/<id>/update"""

    def _url(self, visit_id=1):
        return f'/uk/camera-traps/api/service-visit/{visit_id}/update'

    def _valid_payload(self):
        return {
            'visit_datetime':        '2026-04-10T10:00',
            'visit_purpose_id':      '1',
            'battery_type_id':       '',
            'is_camera_operational': 'true',
            'sd_card_changed':       False,
            'photos_on_card':        '',
            'comments':              'Тест',
        }

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected_to_dashboard(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_manager_can_edit_own_visit_with_access(self):
        """Manager edits their own record, location is accessible."""
        visit = _make_visit(id=1, user_id=self.manager.id)
        session = _make_update_visit_session(visit, has_access=True)
        resp = self._post(self._url(1), self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    def test_manager_cannot_edit_others_visit(self):
        """Manager cannot edit someone else's record -> 403."""
        visit = _make_visit(id=2, user_id=self.admin.id)
        session = _make_update_visit_session(visit, has_access=True)
        resp = self._post(self._url(2), self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_manager_cannot_edit_own_visit_if_location_not_accessible(self):
        """Own record, but location not in the manager's institutions -> 403."""
        visit = _make_visit(id=3, user_id=self.manager.id)
        session = _make_update_visit_session(visit, has_access=False)
        resp = self._post(self._url(3), self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_edit_any_visit(self):
        """Admin edits any record without an institution check."""
        visit = _make_visit(id=3, user_id=self.manager.id)
        session = _make_update_visit_session(visit)
        resp = self._post(self._url(3), self._valid_payload(),
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        # Admin does not call execute() for the institution check
        session.execute.assert_not_called()

    def test_visit_not_found_returns_404(self):
        session = _make_update_visit_session(visit_mock=None)
        resp = self._post(self._url(999), self._valid_payload(),
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 404)

    def test_missing_visit_datetime_returns_400(self):
        visit = _make_visit(id=1, user_id=self.manager.id)
        session = _make_update_visit_session(visit, has_access=True)
        payload = self._valid_payload()
        del payload['visit_datetime']
        resp = self._post(self._url(1), payload,
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 400)

    def test_missing_visit_purpose_returns_400(self):
        visit = _make_visit(id=1, user_id=self.manager.id)
        session = _make_update_visit_session(visit, has_access=True)
        payload = self._valid_payload()
        del payload['visit_purpose_id']
        resp = self._post(self._url(1), payload,
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 400)

    def test_session_commit_called_on_success(self):
        visit = _make_visit(id=1, user_id=self.manager.id)
        session = _make_update_visit_session(visit, has_access=True)
        self._post(self._url(1), self._valid_payload(),
                   user_id=self.manager.id, session=session)
        session.commit.assert_called_once()

    def test_manager2_cannot_edit_manager1_visit(self):
        """Different managers are protected from each other."""
        visit = _make_visit(id=10, user_id=self.manager.id)
        session = _make_update_visit_session(visit, has_access=True)
        resp = self._post(self._url(10), self._valid_payload(),
                          user_id=self.manager2.id, session=session)
        self.assertEqual(resp.status_code, 403)


# ════════════════════════════════════════════════════════════════════════════
# 8. API CREATE LOCATION — MANAGER+ WITH INSTITUTION CHECK
# ════════════════════════════════════════════════════════════════════════════

class TestCtCreateLocation(CtManageLocationsBase):
    """POST /camera-traps/api/location/create — now manager+ with institution."""

    URL = '/uk/camera-traps/api/location/create'

    def _valid_payload(self, institution_id=None):
        return {
            'name':            'Нова тестова локація',
            'description':     'Тестовий ліс',
            'lat':             49.85,
            'lon':             23.65,
            'biotope_ids':     [],
            'institution_ids': [institution_id] if institution_id is not None else [],
        }

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected_to_dashboard(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_manager_can_create_with_own_institution(self):
        """Manager can create a location for their own institution."""
        session = _make_create_location_session()
        mock_loc = MagicMock()
        mock_loc.id = 42

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'), \
             patch('app.camera_traps.routes.Location', return_value=mock_loc):
            _login(self.client, self.manager.id)
            resp = self.client.post(
                self.URL,
                json=self._valid_payload(institution_id=self.inst_a.id),
                headers={'X-CSRFToken': 'test'}
            )

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.get_json()['success'])
        self.assertEqual(resp.get_json()['location_id'], 42)

    def test_manager_cannot_use_foreign_institution(self):
        """Manager A cannot assign inst_b -> 403."""
        session = _make_create_location_session()
        _login(self.client, self.manager.id)
        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'):
            resp = self.client.post(
                self.URL,
                json=self._valid_payload(institution_id=self.inst_b.id),
                headers={'X-CSRFToken': 'test'}
            )
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_create_without_institution(self):
        """Manager can create a location without an institution."""
        session = _make_create_location_session()
        mock_loc = MagicMock()
        mock_loc.id = 55

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'), \
             patch('app.camera_traps.routes.Location', return_value=mock_loc):
            _login(self.client, self.manager.id)
            resp = self.client.post(
                self.URL,
                json=self._valid_payload(institution_id=None),
                headers={'X-CSRFToken': 'test'}
            )

        self.assertEqual(resp.status_code, 201)

    def test_admin_can_create_with_any_institution(self):
        """Admin can assign any institution."""
        session = _make_create_location_session()
        mock_loc = MagicMock()
        mock_loc.id = 99

        with patch('app.camera_traps.routes.get_ct_session', return_value=session), \
             patch('app.camera_traps.routes.close_ct_session'), \
             patch('app.camera_traps.routes.Location', return_value=mock_loc):
            _login(self.client, self.admin.id)
            resp = self.client.post(
                self.URL,
                json=self._valid_payload(institution_id=self.inst_b.id),
                headers={'X-CSRFToken': 'test'}
            )

        self.assertEqual(resp.status_code, 201)

    def test_missing_name_returns_400(self):
        session = _make_create_location_session()
        payload = self._valid_payload()
        del payload['name']
        resp = self._post(self.URL, payload, user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 400)

    def test_empty_name_returns_400(self):
        session = _make_create_location_session()
        payload = self._valid_payload()
        payload['name'] = '   '
        resp = self._post(self.URL, payload, user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 400)

    def test_missing_lat_returns_400(self):
        session = _make_create_location_session()
        payload = self._valid_payload()
        del payload['lat']
        resp = self._post(self.URL, payload, user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 400)

    def test_missing_lon_returns_400(self):
        session = _make_create_location_session()
        payload = self._valid_payload()
        del payload['lon']
        resp = self._post(self.URL, payload, user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 400)


# ════════════════════════════════════════════════════════════════════════════
# 9. API UPDATE LOCATION — MANAGER+ WITH INSTITUTION CHECK
# ════════════════════════════════════════════════════════════════════════════

class TestCtUpdateLocation(CtManageLocationsBase):
    """POST /camera-traps/api/update-location/<id> — now manager+ with institution."""

    def _url(self, location_id=1):
        return f'/uk/camera-traps/api/update-location/{location_id}'

    def _valid_payload(self):
        return {
            'name':        'Оновлена локація',
            'description': 'Новий опис',
            'latitude':    49.90,
            'longitude':   23.70,
            'biotope_ids': [],
        }

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected_to_dashboard(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_manager_can_update_accessible_location(self):
        """Manager updates a location of their own institution."""
        session = _make_update_location_session(has_access=True)
        resp = self._post(self._url(), self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    def test_manager_cannot_update_inaccessible_location(self):
        """Location not in the manager's institutions -> 403."""
        session = _make_update_location_session(has_access=False)
        resp = self._post(self._url(999), self._valid_payload(),
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_manager_without_institution_gets_403(self):
        session = _make_update_location_session()
        resp = self._post(self._url(), self._valid_payload(),
                          user_id=self.manager_no_inst.id, session=session)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_update_any_location(self):
        """Admin updates without an institution check."""
        session = _make_update_location_session(has_access=True)
        resp = self._post(self._url(), self._valid_payload(),
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        session.execute.assert_not_called()

    def test_location_not_found_returns_404(self):
        session = MagicMock()
        session.execute.return_value.fetchone.return_value = (1,)
        session.query.return_value.get.return_value = None
        resp = self._post(self._url(999), self._valid_payload(),
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 404)

    def test_session_commit_called_on_success(self):
        session = _make_update_location_session(has_access=True)
        self._post(self._url(), self._valid_payload(),
                   user_id=self.manager.id, session=session)
        session.commit.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# 10. SET LOCATION VALIDITY — ADMIN-ONLY + JSON-РЕГРЕСІЯ
# ════════════════════════════════════════════════════════════════════════════

def _make_validity_session(location=None):
    """Mock ct_session для set_location_validity: .query(Location).get(id)."""
    mock_session = MagicMock()
    q = MagicMock()
    q.get.return_value = location
    mock_session.query.return_value = q
    return mock_session


class TestCtSetLocationValidity(CtManageLocationsBase):
    """POST /camera-traps/location/<id>/validity — admin-only прапорець валідності.

    Регресія: фронтовий fetch() шле тіло як application/json, але БЕЗ заголовків
    Accept / X-Requested-With. Маршрут має повертати JSON (через request.is_json),
    а не 302-редирект на HTML — інакше r.json() на клієнті падає і показує "Error."
    попри успішний commit у БД.
    """

    def _url(self, location_id=1):
        return f'/uk/camera-traps/location/{location_id}/validity'

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self._url(), json={'is_valid': False})
        self.assertEqual(resp.status_code, 302)

    def test_viewer_is_redirected(self):
        """Не-адмін (viewer) → redirect, бо @role_required('admin')."""
        session = _make_validity_session(_make_location(1))
        resp = self._post(self._url(), {'is_valid': False},
                          user_id=self.viewer.id, session=session)
        self.assertEqual(resp.status_code, 302)

    def test_manager_is_redirected(self):
        """Менеджер не адмін → redirect."""
        session = _make_validity_session(_make_location(1))
        resp = self._post(self._url(), {'is_valid': False},
                          user_id=self.manager.id, session=session)
        self.assertEqual(resp.status_code, 302)

    def test_admin_json_post_returns_json_not_redirect(self):
        """Ядро бага: JSON-тіло без Accept/X-Requested-With → має бути JSON 200."""
        session = _make_validity_session(_make_location(1))
        resp = self._post(self._url(1), {'is_valid': False, 'note': 'тест'},
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type.split(';')[0], 'application/json')
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertFalse(data['is_valid'])

    def test_admin_marks_invalid_persists_note_and_commits(self):
        loc = _make_location(1)
        session = _make_validity_session(loc)
        self._post(self._url(1), {'is_valid': False, 'note': 'погані дані'},
                   user_id=self.admin.id, session=session)
        self.assertFalse(loc.is_valid)
        self.assertEqual(loc.invalid_note, 'погані дані')
        session.commit.assert_called_once()

    def test_admin_marks_valid_clears_note(self):
        loc = _make_location(1)
        session = _make_validity_session(loc)
        resp = self._post(self._url(1), {'is_valid': True, 'note': 'ignored'},
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(loc.is_valid)
        self.assertIsNone(loc.invalid_note)
        self.assertTrue(resp.get_json()['is_valid'])

    def test_location_not_found_returns_json_404(self):
        session = _make_validity_session(location=None)
        resp = self._post(self._url(999), {'is_valid': False},
                          user_id=self.admin.id, session=session)
        self.assertEqual(resp.status_code, 404)
        self.assertIn('error', resp.get_json())


if __name__ == '__main__':
    unittest.main(verbosity=2)
