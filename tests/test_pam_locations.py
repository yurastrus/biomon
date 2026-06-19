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
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.manager)

        # Manager B — inst_b only
        self.manager_b = User(username='manager_b', password_hash=pw)
        self.manager_b.roles.append(self.role_manager)
        self.manager_b.institution_links.append(
            UserInstitution(institution_id=self.inst_b.id, can_export=False)
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
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
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
        # Three sequential executes: recordings, locations, service_visit
        recs   = MagicMock(); recs.fetchall.return_value = []
        loc_row = _make_location_row(101, 'PAM Локація')
        locs   = MagicMock(); locs.fetchall.return_value = [loc_row]
        visit  = MagicMock(); visit.fetchone.return_value = None

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs, visit]

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
        recs  = MagicMock(); recs.fetchall.return_value = []
        row   = _make_location_row(5, 'Адмін-локація')
        locs  = MagicMock(); locs.fetchall.return_value = [row]
        visit = MagicMock(); visit.fetchone.return_value = None

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs, visit]

        _login(self.client, self.admin.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_institution_filter_param_accepted_without_error(self):
        """?institution_id=X does not cause an error."""
        recs  = MagicMock(); recs.fetchall.return_value = []
        locs  = MagicMock(); locs.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs]

        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(f'{self.URL}?institution_id={self.inst_a.id}')

        self.assertEqual(resp.status_code, 200)

    def test_institution_filter_param_injected_into_sql_params(self):
        """selected_inst_id ends up in the SQL query parameters."""
        recs  = MagicMock(); recs.fetchall.return_value = []
        locs  = MagicMock(); locs.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs]

        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            self.client.get(f'{self.URL}?institution_id={self.inst_a.id}')

        # Second call — locations query; check the parameters
        calls = mock_conn.execute.call_args_list
        if len(calls) >= 2:
            _, loc_params = calls[1][0]
            self.assertIn('selected_inst_id', loc_params)
            self.assertEqual(loc_params['selected_inst_id'], self.inst_a.id)

    def test_location_response_has_required_fields(self):
        """Each JSON item contains the expected keys."""
        recs  = MagicMock(); recs.fetchall.return_value = []
        row   = _make_location_row(77, 'Перевірка полів')
        locs  = MagicMock(); locs.fetchall.return_value = [row]
        visit = MagicMock(); visit.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs, visit]

        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)

        item = resp.get_json()[0]
        for key in ('id', 'name', 'latitude', 'longitude', 'status', 'last_visit_date', 'status_reason'):
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
    """Checks the battery_days_left / sd_card_days_left and status computation."""

    URL = '/uk/api/pam/locations-with-status'

    def _make_visit_row(self, days_ago, recording_hours, estimated_hours,
                        capacity_gb, purpose_id=1):
        """Mock row of the latest service visit (with JOIN battery_types/sd_card_status)."""
        row = MagicMock()
        # Naive datetime (tzinfo=None) — the route does datetime.now(tzinfo)
        row.visit_datetime = datetime.now() - timedelta(days=days_ago)
        row.recording_hours_per_day = recording_hours
        row.visit_purpose_id = purpose_id
        row.estimated_recording_hours = estimated_hours
        row.capacity_gb = capacity_gb
        return row

    def _fetch(self, visit_row, location_id=101):
        recs  = MagicMock(); recs.fetchall.return_value = []          # recordings — empty
        loc   = _make_location_row(location_id, 'PAM Локація')
        locs  = MagicMock(); locs.fetchall.return_value = [loc]
        visit = MagicMock(); visit.fetchone.return_value = visit_row
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs, visit]
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()[0]

    def test_battery_and_sd_forecast_computed(self):
        """
        10 days since visit, 6 h/day, battery rated for 600 h, 128 GB card.
        battery_days_left = round(600/6 - 10) = 90
        daily_gb = 6*290/1024 = 1.6992; sd = 128/1.6992 - 10 ~ 65
        """
        row = self._make_visit_row(days_ago=10, recording_hours=6,
                                   estimated_hours=600, capacity_gb=128)
        item = self._fetch(row)
        self.assertEqual(item['battery_days_left'], 90)
        self.assertEqual(item['sd_card_days_left'], 65)
        self.assertEqual(item['status'], 'ok')
        self.assertEqual(item['days_since_visit'], 10)

    def test_battery_critical_when_few_days_left(self):
        """Battery almost dead -> critical (<=3 days left)."""
        # 98 days since visit, battery 600 h / 6 = 100 days -> 2 days left
        row = self._make_visit_row(days_ago=98, recording_hours=6,
                                   estimated_hours=600, capacity_gb=512)
        item = self._fetch(row)
        self.assertEqual(item['battery_days_left'], 2)
        self.assertEqual(item['status'], 'critical')

    def test_device_removed_marks_inactive(self):
        """visit_purpose_id == 3 (dismounted) -> status inactive, no forecast."""
        row = self._make_visit_row(days_ago=5, recording_hours=6,
                                   estimated_hours=600, capacity_gb=128, purpose_id=3)
        item = self._fetch(row)
        self.assertEqual(item['status'], 'inactive')
        self.assertIsNone(item['battery_days_left'])
        self.assertIsNone(item['sd_card_days_left'])

    def test_no_battery_data_leaves_battery_forecast_none(self):
        """Without estimated_recording_hours the battery forecast = None, SD is still computed."""
        row = self._make_visit_row(days_ago=10, recording_hours=6,
                                   estimated_hours=None, capacity_gb=128)
        item = self._fetch(row)
        self.assertIsNone(item['battery_days_left'])
        self.assertEqual(item['sd_card_days_left'], 65)

    def _fetch_with_recordings(self, visit_row, last_recording_days_ago, location_id=101):
        """Like _fetch, but with a non-empty recordings table (old activity date)."""
        rec_row = MagicMock()
        rec_row.location_id = location_id
        rec_row.last_data_date = datetime.now() - timedelta(days=last_recording_days_ago)
        recs  = MagicMock(); recs.fetchall.return_value = [rec_row]
        loc   = _make_location_row(location_id, 'PAM Локація')
        locs  = MagicMock(); locs.fetchall.return_value = [loc]
        visit = MagicMock(); visit.fetchone.return_value = visit_row
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [recs, locs, visit]
        _login(self.client, self.manager.id)
        with patch('app.pam.routes.get_pam_db_connection', return_value=mock_conn):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()[0]

    def test_old_recordings_do_not_override_fresh_install(self):
        """
        REGRESSION (reinstall): autumn recordings 240 days ago must NOT mark the location
        inactive if a fresh install was logged in spring (16 days, purpose != removal).
        """
        visit = self._make_visit_row(days_ago=16, recording_hours=6,
                                     estimated_hours=600, capacity_gb=64, purpose_id=2)
        item = self._fetch_with_recordings(visit, last_recording_days_ago=240)
        self.assertNotEqual(item['status'], 'inactive')      # key point: NOT greyed out
        self.assertIsNotNone(item['battery_days_left'])      # forecast was computed
        self.assertEqual(item['days_since_visit'], 16)

    def test_old_recordings_and_old_visit_still_inactive(self):
        """If both the data AND the last visit are old (>200 days) -- stays inactive."""
        visit = self._make_visit_row(days_ago=250, recording_hours=6,
                                     estimated_hours=600, capacity_gb=64, purpose_id=1)
        item = self._fetch_with_recordings(visit, last_recording_days_ago=240)
        self.assertEqual(item['status'], 'inactive')


if __name__ == '__main__':
    unittest.main(verbosity=2)
