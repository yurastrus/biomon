"""
Integration tests for managing and creating PAM locations.

Covers:
  - manage_pam_locations (GET)          — access, filtering by institution
  - api_get_pam_locations_with_status   — access, institution filter, empty result
  - api_get_pam_service_history         — access guard by institution
  - api_create_pam_service_visit        — access guard + field validation
  - api_create_pam_location             — access, validation, correct INSERT
  - update_pam_location                 — access, guard against assigning other institutions

Run:
    venv/Scripts/python -m unittest tests.test_pam_locations -v
"""

import os
import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta


# ── helpers ────────────────────────────────────────────────────────────────

def _login(client, user_id):
    """Sets up a Flask-Login session without an HTTP request."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _make_location_row(location_id=1, name='Тестова локація', lat=49.85, lon=23.65,
                       name_en='Test Location', institution_id=None, state_province='Lviv Oblast'):
    """Mock row of the locations table (with LEFT JOIN location_institutions)."""
    row = MagicMock()
    row.location_id = location_id
    row.location_name = name
    row.location_name_en = name_en
    row.lat = lat
    row.lon = lon
    row.institution_id = institution_id
    row.state_province = state_province
    return row


def _make_biotope_row(id=1, name_ua='Ліс', name_en='Forest'):
    """Mock row of the biotopes table."""
    row = MagicMock()
    row._mapping = {'id': id, 'name_ua': name_ua, 'name_en': name_en}
    return row


def _make_pam_manage_conn(location_rows=(), biotope_rows=()):
    """
    Mock conn for manage_pam_locations: 6 sequential fetchall() calls:
    1 — locations with location_institutions,
    2 — location_biotopes (biotope_links),
    3 — biotopes (list for the form),
    4 — battery_types,
    5 — sd_card_status,
    6 — visit_purposes.
    """
    mock_conn = MagicMock()
    loc_result = MagicMock()
    loc_result.fetchall.return_value = list(location_rows)
    bio_links_result = MagicMock()
    bio_links_result.fetchall.return_value = []
    bio_result = MagicMock()
    bio_result.fetchall.return_value = list(biotope_rows)
    empty = MagicMock()
    empty.fetchall.return_value = []
    mock_conn.execute.side_effect = [
        loc_result,       # 1. locations LEFT JOIN location_institutions
        bio_links_result, # 2. location_biotopes
        bio_result,       # 3. biotopes
        empty,            # 4. battery_types
        empty,            # 5. sd_card_status
        empty,            # 6. visit_purposes
    ]
    return mock_conn


def _make_pam_conn_for_create(new_id=42):
    """
    Mock conn for api_create_pam_location:
    the first execute() returns (location_id,) from RETURNING.
    """
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (new_id,)
    mock_conn.execute.return_value = mock_result
    return mock_conn


def _make_access_conn(has_access=True):
    """
    Mock conn where the first fetchone() is the access check
    (returning (1,) or None), then an unbounded MagicMock for the rest.
    """
    mock_conn = MagicMock()
    access_result = MagicMock()
    access_result.fetchone.return_value = (1,) if has_access else None
    # Second execute — history/insert — may return anything
    rest_result = MagicMock()
    rest_result.fetchall.return_value = []
    rest_result.fetchone.return_value = None
    mock_conn.execute.side_effect = [access_result, rest_result]
    return mock_conn


def _make_status_conn(location_rows, last_rec_rows=(), visit_rows=()):
    """
    Mock conn for api_get_pam_locations_with_status, three executes:
    1. locations (access-filtered), 2. last recording per location,
    3. service visits with per-interval recording aggregates.
    """
    mock_conn = MagicMock()
    locs = MagicMock(); locs.fetchall.return_value = list(location_rows)
    last = MagicMock(); last.fetchall.return_value = list(last_rec_rows)
    visits = MagicMock(); visits.fetchall.return_value = list(visit_rows)
    mock_conn.execute.side_effect = [locs, last, visits]
    return mock_conn


def _make_last_rec_row(location_id, days_ago):
    row = MagicMock()
    row.location_id = location_id
    row.last_data_date = datetime.now() - timedelta(days=days_ago)
    return row


def _make_visit_row(location_id, days_ago, purpose_id=1, hours=6, battery_type_id=1,
                    rated_hours=300, capacity_gb=59.6, last_recording=None, recorded_hours=0):
    """Row of routes.SERVICE_VISITS_SQL (naive datetimes are fine for the maths)."""
    row = MagicMock()
    row.location_id = location_id
    row.visit_datetime = datetime.now() - timedelta(days=days_ago)
    row.visit_purpose_id = purpose_id
    row.recording_hours_per_day = hours
    row.battery_type_id = battery_type_id
    row.is_operational = None
    row.estimated_recording_hours = rated_hours
    row.capacity_gb = capacity_gb
    row.last_recording = last_recording
    row.recorded_hours = recorded_hours
    return row


# ── base test class ────────────────────────────────────────────────────────

class PamLocationTestBase(unittest.TestCase):

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

        self.role_admin        = Role(name='admin')
        self.role_manager      = Role(name='manager')
        self.role_pam_verifier = Role(name='pam_verifier')
        self.role_viewer       = Role(name='viewer')
        db.session.add_all([
            self.role_admin, self.role_manager,
            self.role_pam_verifier, self.role_viewer,
        ])
        db.session.flush()

        self.inst_a = Institution(name_uk='Заповідник А', name_en='Reserve A', code='res_a')
        self.inst_b = Institution(name_uk='Заповідник Б', name_en='Reserve B', code='res_b')
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('testpass').decode('utf-8')

        # Admin — sees everything
        self.admin = User(username='admin_user', password_hash=pw)
        self.admin.roles.append(self.role_admin)
        db.session.add(self.admin)

        # Manager A — inst_a only
        self.manager = User(username='manager_a', password_hash=pw)
        self.manager.roles.append(self.role_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True, can_view_pam=True,
                can_manage_ct=True, can_manage_pam=True)
        )
        db.session.add(self.manager)

        # Manager B — inst_b only
        self.manager_b = User(username='manager_b', password_hash=pw)
        self.manager_b.roles.append(self.role_manager)
        self.manager_b.institution_links.append(
            UserInstitution(institution_id=self.inst_b.id, can_view_ct=True, can_view_pam=True,
                can_manage_ct=True, can_manage_pam=True)
        )
        db.session.add(self.manager_b)

        # Manager with no institutions
        self.manager_no_inst = User(username='manager_no_inst', password_hash=pw)
        self.manager_no_inst.roles.append(self.role_manager)
        db.session.add(self.manager_no_inst)

        # PAM verifier — inst_a
        self.pam_verifier = User(username='pam_verifier_user', password_hash=pw)
        self.pam_verifier.roles.append(self.role_pam_verifier)
        self.pam_verifier.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True, can_view_pam=True)
        )
        db.session.add(self.pam_verifier)

        # Viewer — no institutions
        self.viewer = User(username='viewer_user', password_hash=pw)
        self.viewer.roles.append(self.role_viewer)
        db.session.add(self.viewer)

        db.session.commit()


# ════════════════════════════════════════════════════════════════════════════
# 1. MANAGE PAM LOCATIONS — ACCESS
# ════════════════════════════════════════════════════════════════════════════

class TestManagePamLocationsAccess(PamLocationTestBase):
    """GET /pam/manage-locations — who can access it."""

    URL = '/uk/pam/manage-locations'
    _EMPTY_CONN = staticmethod(lambda: _make_pam_manage_conn())

    def test_anonymous_is_redirected(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)

    def test_viewer_gets_403(self):
        _login(self.client, self.viewer.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._EMPTY_CONN()):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 403)

    def test_pam_verifier_can_access(self):
        """pam_verifier now has access (minimum role for the merged page)."""
        _login(self.client, self.pam_verifier.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._EMPTY_CONN()):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)

    def test_manager_can_access(self):
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._EMPTY_CONN()):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_access(self):
        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=self._EMPTY_CONN()):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════════════════════
# 2. MANAGE PAM LOCATIONS — CONTENT FILTERING
# ════════════════════════════════════════════════════════════════════════════

class TestManagePamLocationsContent(PamLocationTestBase):
    """
    manage_pam_locations filters in Python:
    a manager sees only locations of their own institution.
    """

    URL = '/uk/pam/manage-locations'

    def _get_with_mock_locations(self, user_id, rows):
        mock_conn = _make_pam_manage_conn(location_rows=rows)
        _login(self.client, user_id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            return self.client.get(self.URL)

    def test_admin_sees_all_locations(self):
        rows = [
            _make_location_row(1, 'Локація А', institution_id=self.inst_a.id),
            _make_location_row(2, 'Локація Б', institution_id=self.inst_b.id),
        ]
        resp = self._get_with_mock_locations(self.admin.id, rows)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Локація А'.encode(), resp.data)
        self.assertIn('Локація Б'.encode(), resp.data)

    def test_manager_sees_only_own_institution_locations(self):
        rows = [
            _make_location_row(1, 'Власна локація', institution_id=self.inst_a.id),
            _make_location_row(2, 'Чужа локація',   institution_id=self.inst_b.id),
        ]
        resp = self._get_with_mock_locations(self.manager.id, rows)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Власна локація'.encode(), resp.data)
        self.assertNotIn('Чужа локація'.encode(), resp.data)

    def test_manager_with_no_institutions_sees_no_locations(self):
        rows = [
            _make_location_row(1, 'Якась локація', institution_id=self.inst_a.id),
        ]
        resp = self._get_with_mock_locations(self.manager_no_inst.id, rows)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Якась локація'.encode(), resp.data)

    def test_manager_b_does_not_see_inst_a_locations(self):
        rows = [
            _make_location_row(1, 'Локація А-1', institution_id=self.inst_a.id),
            _make_location_row(2, 'Локація Б-1', institution_id=self.inst_b.id),
        ]
        resp = self._get_with_mock_locations(self.manager_b.id, rows)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Локація А-1'.encode(), resp.data)
        self.assertIn('Локація Б-1'.encode(), resp.data)


# ════════════════════════════════════════════════════════════════════════════
# 3. API LOCATIONS-WITH-STATUS — ACCESS AND BASIC BEHAVIOR
# ════════════════════════════════════════════════════════════════════════════

class TestPamLocationsWithStatus(PamLocationTestBase):
    """GET /api/pam/locations-with-status"""

    URL = '/uk/api/pam/locations-with-status'

    def test_anonymous_is_redirected(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)

    def test_manager_with_no_institutions_returns_empty_list_without_db_call(self):
        """The route returns [] immediately, without touching the PAM DB."""
        mock_conn = MagicMock()
        _login(self.client, self.manager_no_inst.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn) as mock_get:
            resp = self.client.get(self.URL)
        # Early response — PAM DB was not opened
        mock_get.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_viewer_with_no_institutions_returns_empty_list(self):
        _login(self.client, self.viewer.id)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_manager_with_institutions_returns_json_list(self):
        """With mock data, returns a list with a single record."""
        mock_conn = _make_status_conn([_make_location_row(101, 'PAM Локація')])
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['id'], 101)
        self.assertEqual(data[0]['name'], 'PAM Локація')

    def test_admin_gets_json_list(self):
        """Admin is not restricted by institutions."""
        mock_conn = _make_status_conn([_make_location_row(5, 'Адмін-локація')])
        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_no_locations_skips_heavy_queries(self):
        """Empty location list → [] without reading recordings or visits."""
        mock_conn = _make_status_conn([])
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(f'{self.URL}?institution_id={self.inst_a.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])
        self.assertEqual(mock_conn.execute.call_count, 1)

    def test_institution_filter_param_injected_into_sql_params(self):
        """selected_inst_id ends up in the SQL query parameters."""
        mock_conn = _make_status_conn([])
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.get(f'{self.URL}?institution_id={self.inst_a.id}')
        _, loc_params = mock_conn.execute.call_args_list[0][0]
        self.assertEqual(loc_params['selected_inst_id'], self.inst_a.id)

    def test_location_filter_param_injected_into_sql_params(self):
        """?location_id=X (forecast block of the panel) narrows the locations query."""
        mock_conn = _make_status_conn([])
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.get(f'{self.URL}?location_id=77')
        sql, loc_params = mock_conn.execute.call_args_list[0][0]
        self.assertEqual(loc_params['selected_loc_id'], 77)
        self.assertIn('li_perm', str(sql))   # access condition still applies

    def test_location_response_has_required_fields(self):
        """Each JSON item contains the expected keys."""
        mock_conn = _make_status_conn([_make_location_row(77, 'Перевірка полів')])
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)

        item = resp.get_json()[0]
        for key in ('id', 'name', 'latitude', 'longitude', 'status', 'last_visit_date',
                    'status_reason', 'visit_by', 'limiting', 'lost_days', 'battery_source',
                    'last_purpose_id', 'last_hours_per_day'):
            self.assertIn(key, item, f"Missing key: {key}")


# ════════════════════════════════════════════════════════════════════════════
# 4. API SERVICE HISTORY — ACCESS GUARD
# ════════════════════════════════════════════════════════════════════════════

class TestPamServiceHistoryAccess(PamLocationTestBase):
    """GET /api/pam/location/<id>/service-history — access guard check."""

    def _url(self, location_id=101):
        return f'/uk/api/pam/location/{location_id}/service-history'

    def test_anonymous_is_redirected(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)

    def test_user_with_no_institutions_gets_403(self):
        """A viewer with no institutions cannot read any history."""
        _login(self.client, self.viewer.id)
        mock_conn = MagicMock()
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_manager_with_access_gets_200(self):
        """A manager with access — gets a response."""
        mock_conn = _make_access_conn(has_access=True)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_manager_without_access_gets_403(self):
        """A manager without access to the location gets 403."""
        mock_conn = _make_access_conn(has_access=False)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self._url(location_id=999))
        self.assertEqual(resp.status_code, 403)
        self.assertIn('error', resp.get_json())

    def test_admin_bypasses_access_check(self):
        """Admin skips the institution check — sees any location."""
        # Only one execute — history query (no access check)
        history_mock = MagicMock()
        history_mock.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.return_value = history_mock

        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self._url(location_id=999))

        self.assertEqual(resp.status_code, 200)
        # Admin does not invoke the access check — exactly one call (history)
        self.assertEqual(mock_conn.execute.call_count, 1)


# ════════════════════════════════════════════════════════════════════════════
# 5. API CREATE SERVICE VISIT — ACCESS AND VALIDATION
# ════════════════════════════════════════════════════════════════════════════

class TestPamCreateServiceVisit(PamLocationTestBase):
    """POST /api/pam/service-log/create"""

    URL = '/uk/api/pam/service-log/create'

    def _valid_payload(self, location_id=101):
        return {
            'location_id':           str(location_id),
            'visit_datetime':        '2026-04-17T10:00',
            'visit_purpose_id':      '1',
            'sd_card_status_id':     '1',
            'recording_hours_per_day': '6',
            'battery_type_id':       '',
            'is_camera_operational': '',
            'comments':              '',
        }

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_pam_verifier_gets_403(self):
        """pam_verifier cannot create records (manager required)."""
        _login(self.client, self.pam_verifier.id)
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_viewer_gets_403(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_manager_with_access_creates_visit_successfully(self):
        """A manager with access — 201."""
        mock_conn = _make_access_conn(has_access=True)
        mock_conn.commit = MagicMock()

        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json=self._valid_payload())

        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data['success'])

    def test_manager_without_access_gets_403(self):
        """A manager without access to the location — 403."""
        mock_conn = _make_access_conn(has_access=False)

        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json=self._valid_payload(location_id=999))

        self.assertEqual(resp.status_code, 403)

    def test_admin_can_create_without_institution_check(self):
        """Admin skips the access check — straight to INSERT."""
        insert_mock = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value = insert_mock
        mock_conn.commit = MagicMock()

        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json=self._valid_payload())

        self.assertEqual(resp.status_code, 201)
        # INSERT only — a single execute call (no access check)
        self.assertEqual(mock_conn.execute.call_count, 1)

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

    def test_missing_sd_card_status_returns_400(self):
        _login(self.client, self.manager.id)
        payload = self._valid_payload()
        del payload['sd_card_status_id']
        resp = self.client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_missing_visit_purpose_returns_400(self):
        _login(self.client, self.manager.id)
        payload = self._valid_payload()
        del payload['visit_purpose_id']
        resp = self.client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_missing_recording_hours_returns_400(self):
        _login(self.client, self.manager.id)
        payload = self._valid_payload()
        del payload['recording_hours_per_day']
        resp = self.client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_manager_with_no_institutions_gets_403(self):
        mock_conn = MagicMock()
        _login(self.client, self.manager_no_inst.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)


# ════════════════════════════════════════════════════════════════════════════
# 6. API CREATE LOCATION — ACCESS
# ════════════════════════════════════════════════════════════════════════════

class TestPamCreateLocationAccess(PamLocationTestBase):
    """POST /pam/api/location/create — who can create locations."""

    URL = '/uk/pam/api/location/create'

    def _valid_payload(self, institution_ids=None):
        return {
            'name':            'Нова тестова локація',
            'name_en':         'New Test Location',
            'state_province':  'Lviv Oblast',
            'lat':             49.85,
            'lon':             23.65,
            'institution_ids': institution_ids if institution_ids is not None else [],
            'biotope_ids':     [],
        }

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_gets_403(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_pam_verifier_gets_403(self):
        """pam_verifier cannot create locations (manager/admin required)."""
        _login(self.client, self.pam_verifier.id)
        resp = self.client.post(self.URL, json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_create_location(self):
        mock_conn = _make_pam_conn_for_create(new_id=42)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(
                self.URL,
                json=self._valid_payload(institution_ids=[self.inst_a.id])
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['location_id'], 42)

    def test_admin_can_create_location(self):
        mock_conn = _make_pam_conn_for_create(new_id=99)
        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(
                self.URL,
                json=self._valid_payload(institution_ids=[self.inst_a.id, self.inst_b.id])
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['location_id'], 99)


# ════════════════════════════════════════════════════════════════════════════
# 7. API CREATE LOCATION — INPUT VALIDATION
# ════════════════════════════════════════════════════════════════════════════

class TestPamCreateLocationValidation(PamLocationTestBase):
    """Field validation in api_create_pam_location."""

    URL = '/uk/pam/api/location/create'

    def _post(self, payload):
        _login(self.client, self.manager.id)
        return self.client.post(self.URL, json=payload)

    def test_missing_name_returns_400(self):
        resp = self._post({'lat': 49.85, 'lon': 23.65, 'institution_ids': [], 'biotope_ids': []})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])

    def test_empty_name_returns_400(self):
        resp = self._post({'name': '', 'lat': 49.85, 'lon': 23.65, 'institution_ids': [], 'biotope_ids': []})
        self.assertEqual(resp.status_code, 400)

    def test_missing_lat_returns_400(self):
        resp = self._post({'name': 'Test', 'lon': 23.65, 'institution_ids': [], 'biotope_ids': []})
        self.assertEqual(resp.status_code, 400)

    def test_missing_lon_returns_400(self):
        resp = self._post({'name': 'Test', 'lat': 49.85, 'institution_ids': [], 'biotope_ids': []})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_lat_type_returns_400(self):
        resp = self._post({'name': 'Test', 'lat': 'not_a_number', 'lon': 23.65,
                           'institution_ids': [], 'biotope_ids': []})
        self.assertEqual(resp.status_code, 400)

    def test_manager_cannot_assign_foreign_institution(self):
        """Manager A cannot assign inst_b."""
        resp = self._post({
            'name':            'Test',
            'lat':             49.85,
            'lon':             23.65,
            'institution_ids': [self.inst_b.id],  # foreign institution
            'biotope_ids':     [],
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.get_json()['success'])

    def test_manager_cannot_assign_mixed_institutions(self):
        """Manager A cannot assign inst_a + inst_b at the same time."""
        resp = self._post({
            'name':            'Test',
            'lat':             49.85,
            'lon':             23.65,
            'institution_ids': [self.inst_a.id, self.inst_b.id],
            'biotope_ids':     [],
        })
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_assign_any_institution(self):
        """Admin can assign any combination of institutions."""
        mock_conn = _make_pam_conn_for_create(new_id=10)
        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json={
                'name':            'Admin Location',
                'lat':             49.85,
                'lon':             23.65,
                'institution_ids': [self.inst_a.id, self.inst_b.id],
                'biotope_ids':     [],
            })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    def test_create_without_institutions_is_allowed(self):
        """A location can be created with no institution (institution_ids=[])."""
        mock_conn = _make_pam_conn_for_create(new_id=55)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json={
                'name':            'Без установи',
                'lat':             49.85,
                'lon':             23.65,
                'institution_ids': [],
                'biotope_ids':     [],
            })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])


# ════════════════════════════════════════════════════════════════════════════
# 8. API CREATE LOCATION — INSERT STRUCTURE
# ════════════════════════════════════════════════════════════════════════════

class TestPamCreateLocationInsert(PamLocationTestBase):
    """Checks that the DB INSERT runs with the correct number of calls."""

    URL = '/uk/pam/api/location/create'

    def _create(self, institution_ids=(), biotope_ids=()):
        mock_conn = _make_pam_conn_for_create(new_id=42)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.post(self.URL, json={
                'name':            'Test',
                'lat':             49.85,
                'lon':             23.65,
                'institution_ids': list(institution_ids),
                'biotope_ids':     list(biotope_ids),
            })
        return mock_conn

    def test_insert_locations_always_called(self):
        mock_conn = self._create()
        self.assertTrue(mock_conn.execute.called)

    def test_only_location_insert_when_no_links(self):
        """No institutions and no biotopes — a single execute (INSERT locations)."""
        mock_conn = self._create(institution_ids=[], biotope_ids=[])
        self.assertEqual(mock_conn.execute.call_count, 1)

    def test_two_inserts_when_only_institution(self):
        """With one institution — two executes: INSERT locations + INSERT location_institutions."""
        mock_conn = self._create(institution_ids=[self.inst_a.id], biotope_ids=[])
        self.assertEqual(mock_conn.execute.call_count, 2)

    def test_two_inserts_when_only_biotope(self):
        """With one biotope — two executes: INSERT locations + INSERT location_biotopes."""
        mock_conn = self._create(institution_ids=[], biotope_ids=[1])
        self.assertEqual(mock_conn.execute.call_count, 2)

    def test_three_inserts_when_institution_and_biotope(self):
        """Both institution and biotope — three executes."""
        mock_conn = self._create(institution_ids=[self.inst_a.id], biotope_ids=[1])
        self.assertEqual(mock_conn.execute.call_count, 3)

    def test_begin_transaction_called(self):
        """The operation runs in a transaction (conn.begin() is called)."""
        mock_conn = self._create(institution_ids=[self.inst_a.id])
        mock_conn.begin.assert_called_once()

    def test_response_contains_returned_location_id(self):
        mock_conn = _make_pam_conn_for_create(new_id=777)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json={
                'name': 'ID check', 'lat': 49.0, 'lon': 23.0,
                'institution_ids': [], 'biotope_ids': [],
            })
        self.assertEqual(resp.get_json()['location_id'], 777)

    def test_db_error_returns_500(self):
        """On a DB error, returns 500."""
        mock_conn = MagicMock()
        mock_conn.begin.side_effect = Exception("DB unavailable")

        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json={
                'name': 'Error test', 'lat': 49.0, 'lon': 23.0,
                'institution_ids': [], 'biotope_ids': [],
            })
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.get_json()['success'])


# ════════════════════════════════════════════════════════════════════════════
# 9. UPDATE LOCATION — ACCESS AND PROTECTION
# ════════════════════════════════════════════════════════════════════════════

class TestPamUpdateLocation(PamLocationTestBase):
    """POST /pam/api/update-location/<id>"""

    def _url(self, location_id=101):
        return f'/uk/pam/api/update-location/{location_id}'

    def _valid_payload(self, institution_ids=None):
        return {
            'name':            'Оновлена локація',
            'name_en':         'Updated Location',
            'institution_ids': institution_ids if institution_ids is not None else [],
            'biotope_ids':     [],
        }

    def _make_update_conn(self):
        """Mock conn for a successful UPDATE."""
        mock_conn = MagicMock()
        return mock_conn

    def test_anonymous_is_redirected(self):
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 302)

    def test_viewer_gets_403(self):
        _login(self.client, self.viewer.id)
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_pam_verifier_gets_403(self):
        _login(self.client, self.pam_verifier.id)
        resp = self.client.post(self._url(), json=self._valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_update_with_own_institution(self):
        mock_conn = self._make_update_conn()
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(
                self._url(),
                json=self._valid_payload(institution_ids=[self.inst_a.id])
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    def test_manager_cannot_assign_foreign_institution(self):
        """A manager cannot assign a foreign institution — 403 without DB access."""
        mock_conn = MagicMock()
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(
                self._url(),
                json=self._valid_payload(institution_ids=[self.inst_b.id])
            )
        self.assertEqual(resp.status_code, 403)
        # DB should not be called — the check is in Python
        mock_conn.execute.assert_not_called()

    def test_admin_can_update_with_any_institutions(self):
        mock_conn = self._make_update_conn()
        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(
                self._url(),
                json=self._valid_payload(institution_ids=[self.inst_a.id, self.inst_b.id])
            )
        self.assertEqual(resp.status_code, 200)

    def test_update_executes_in_transaction(self):
        """UPDATE runs via conn.begin()."""
        mock_conn = self._make_update_conn()
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.post(
                self._url(),
                json=self._valid_payload(institution_ids=[self.inst_a.id])
            )
        mock_conn.begin.assert_called_once()

    def test_conn_closed_after_success(self):
        """The connection is closed after a successful run."""
        mock_conn = self._make_update_conn()
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.post(
                self._url(),
                json=self._valid_payload(institution_ids=[self.inst_a.id])
            )
        mock_conn.close.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# 3b. API LOCATIONS-WITH-STATUS — FORECAST MATH (battery/SD days_left)
#     Guards the data consumed by the service-mode marker popup on manage-locations.
# ════════════════════════════════════════════════════════════════════════════

class TestPamLocationStatusForecast(PamLocationTestBase):
    """Route-level forecast: rows from the visits query reach service_forecast."""

    URL = '/uk/api/pam/locations-with-status'

    def _fetch(self, visit_rows, last_rec_days_ago=None, location_id=101):
        last = []
        if last_rec_days_ago is not None:
            last = [_make_last_rec_row(location_id, last_rec_days_ago)]
        mock_conn = _make_status_conn([_make_location_row(location_id, 'PAM Локація')],
                                      last_rec_rows=last, visit_rows=visit_rows)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()[0]

    def test_battery_and_sd_forecast_computed(self):
        """
        10 days since visit, 6 h/day, battery rated 600 h, 64 GB card, no history.
        battery = 600/6 - 10 = 90; sd = 59.6 / (6*165/1024) - 10 ~ 52.
        """
        item = self._fetch([_make_visit_row(101, 10, rated_hours=600, capacity_gb=59.6)])
        self.assertEqual(item['battery_days_left'], 90)
        self.assertEqual(item['sd_card_days_left'], 52)
        self.assertEqual(item['status'], 'ok')
        self.assertEqual(item['days_since_visit'], 10)
        self.assertEqual(item['limiting'], 'sd')
        self.assertEqual(item['battery_source'], 'rated')

    def test_battery_critical_when_few_days_left(self):
        """Battery almost dead -> critical (<=3 days left)."""
        item = self._fetch([_make_visit_row(101, 98, rated_hours=600, capacity_gb=512)])
        self.assertEqual(item['battery_days_left'], 2)
        self.assertEqual(item['status'], 'critical')

    def test_exhausted_forecast_is_overdue(self):
        item = self._fetch([_make_visit_row(101, 120, rated_hours=600, capacity_gb=512)])
        self.assertEqual(item['status'], 'overdue')
        self.assertEqual(item['lost_days'], 20)

    def test_device_removed(self):
        """Last visit = removal -> status removed, no forecast."""
        item = self._fetch([_make_visit_row(101, 5, purpose_id=3, battery_type_id=None,
                                            rated_hours=None, capacity_gb=None)])
        self.assertEqual(item['status'], 'removed')
        self.assertIsNone(item['battery_days_left'])
        self.assertIsNone(item['sd_card_days_left'])

    def test_recordings_without_log_is_no_log(self):
        item = self._fetch([], last_rec_days_ago=20)
        self.assertEqual(item['status'], 'no_log')

    def test_old_recordings_do_not_override_fresh_install(self):
        """
        REGRESSION (reinstall): old recordings must NOT mark the location inactive
        if a fresh install was logged 16 days ago.
        """
        item = self._fetch([_make_visit_row(101, 16, purpose_id=2, rated_hours=600, capacity_gb=64)],
                           last_rec_days_ago=240)
        self.assertNotIn(item['status'], ('inactive', 'removed'))
        self.assertIsNotNone(item['battery_days_left'])
        self.assertEqual(item['days_since_visit'], 16)

    def test_old_recordings_and_old_visit_still_inactive(self):
        """If both the data AND the last visit are old (>200 days) -- inactive."""
        item = self._fetch([_make_visit_row(101, 250, rated_hours=600, capacity_gb=64)],
                           last_rec_days_ago=240)
        self.assertEqual(item['status'], 'inactive')


# ════════════════════════════════════════════════════════════════════════════
# 9. SERVICE LOG — VALIDATION, EDIT/DELETE RIGHTS, SQL PARAMETERS
# ════════════════════════════════════════════════════════════════════════════

def _make_visit_owner_conn(owner_id, location_id=101, has_access=True, found=True):
    """Mock conn for update/delete: visit lookup, (access check), write."""
    visit = MagicMock()
    visit.fetchone.return_value = (MagicMock(location_id=location_id, user_id=owner_id)
                                   if found else None)
    access = MagicMock()
    access.fetchone.return_value = (1,) if has_access else None
    write = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [visit, access, write]
    return mock_conn


class TestPamServiceVisitValidation(PamLocationTestBase):
    """Purpose-dependent fields and sanity checks on create."""

    URL = '/uk/api/pam/service-log/create'

    def _post(self, **overrides):
        payload = {
            'location_id': '101', 'visit_datetime': '2026-04-17T10:00',
            'visit_purpose_id': '1', 'sd_card_status_id': '3',
            'recording_hours_per_day': '6', 'battery_type_id': '1',
            'is_camera_operational': '', 'comments': '',
        }
        payload.update(overrides)
        mock_conn = _make_access_conn(has_access=True)
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(self.URL, json=payload)
        return resp, mock_conn

    def test_removal_needs_no_card_battery_or_schedule(self):
        resp, conn = self._post(visit_purpose_id='3', sd_card_status_id='',
                                recording_hours_per_day='', battery_type_id='4')
        self.assertEqual(resp.status_code, 201)
        params = conn.execute.call_args_list[-1][0][1]
        self.assertEqual(params['sd_card_status_id'], 1)       # card stays in the removed device
        self.assertEqual(params['recording_hours_per_day'], 0)
        self.assertIsNone(params['battery_type_id'])

    def test_routine_visit_still_requires_card_and_schedule(self):
        resp, _ = self._post(sd_card_status_id='')
        self.assertEqual(resp.status_code, 400)

    def test_future_date_rejected(self):
        future = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')
        resp, _ = self._post(visit_datetime=future)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('майбутньому', resp.get_json()['error'])

    def test_hours_out_of_range_rejected(self):
        resp, _ = self._post(recording_hours_per_day='25')
        self.assertEqual(resp.status_code, 400)

    def test_garbage_date_is_400_not_500(self):
        resp, _ = self._post(visit_datetime='not-a-date')
        self.assertEqual(resp.status_code, 400)


class TestPamServiceVisitEditRights(PamLocationTestBase):
    """Only the author (or an admin) may update or delete a record."""

    PAYLOAD = {'visit_datetime': '2026-04-17T10:00', 'visit_purpose_id': '1',
               'sd_card_status_id': '3', 'recording_hours_per_day': '6'}

    def _call(self, action, user, owner_id, **kw):
        mock_conn = _make_visit_owner_conn(owner_id, **kw)
        _login(self.client, user.id)
        url = f'/uk/api/pam/service-visit/55/{action}'
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post(url, json=self.PAYLOAD)
        return resp, mock_conn

    def test_author_can_delete(self):
        resp, conn = self._call('delete', self.manager, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('DELETE FROM service_visits', str(conn.execute.call_args_list[-1][0][0]))
        conn.commit.assert_called_once()

    def test_other_manager_cannot_delete(self):
        resp, conn = self._call('delete', self.manager, owner_id=self.manager_b.id)
        self.assertEqual(resp.status_code, 403)
        conn.commit.assert_not_called()

    def test_other_manager_cannot_update(self):
        resp, conn = self._call('update', self.manager, owner_id=self.manager_b.id)
        self.assertEqual(resp.status_code, 403)
        conn.commit.assert_not_called()

    def test_author_can_update(self):
        resp, conn = self._call('update', self.manager, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        conn.commit.assert_called_once()

    def test_admin_can_delete_any(self):
        mock_conn = MagicMock()
        visit = MagicMock(); visit.fetchone.return_value = MagicMock(location_id=1, user_id=999)
        mock_conn.execute.side_effect = [visit, MagicMock()]   # no access query for admin
        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.post('/uk/api/pam/service-visit/55/delete')
        self.assertEqual(resp.status_code, 200)

    def test_author_without_location_access_cannot_delete(self):
        resp, _ = self._call('delete', self.manager, self.manager.id, has_access=False)
        self.assertEqual(resp.status_code, 403)

    def test_missing_visit_is_404(self):
        resp, _ = self._call('delete', self.manager, self.manager.id, found=False)
        self.assertEqual(resp.status_code, 404)

    def test_verifier_cannot_delete(self):
        _login(self.client, self.pam_verifier.id)
        resp = self.client.post('/uk/api/pam/service-visit/55/delete')
        self.assertEqual(resp.status_code, 403)


class TestPamServiceHistorySqlParams(PamLocationTestBase):
    """lang_code from the URL must reach SQL as a bound parameter, not as text."""

    def test_lang_code_is_bound_not_interpolated(self):
        history = MagicMock(); history.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.return_value = history
        _login(self.client, self.admin.id)
        evil = "uk'--"
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.get(f'/{evil}/api/pam/location/1/service-history')
        sql, params = mock_conn.execute.call_args_list[0][0]
        self.assertNotIn(evil, str(sql))
        self.assertEqual(params['lang'], evil)


if __name__ == '__main__':
    unittest.main(verbosity=2)
