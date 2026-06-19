"""
Tests for the stats/API endpoints of the camera_traps module.

Covers:
  - GET /api/stats/top-species      — response structure
  - GET /api/stats/locations        — response structure
  - GET /api/stats/distribution-map — parameter validation + structure
  - GET /api/stats/daily-activity   — parameter validation + structure
  - GET /api/stats/comparison       — validation + access control
  - GET /api/gallery/photos         — parameter validation + 404 when no photos

Run:
    venv/Scripts/python -m unittest tests.test_ct_stats_api -v
"""

import contextlib
import json
import os
import unittest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════

def _login(client, user_id):
    """Set up a Flask-Login session without an HTTP request."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _generic_session():
    """
    Mock ct_session: any ORM chain returns [], scalar() → 0.
    Raw SQL (session.connection().execute().mappings().fetchall()) → [].
    """
    q = MagicMock()
    for method in ('join', 'outerjoin', 'filter', 'order_by', 'group_by',
                   'having', 'distinct', 'params', 'limit', 'offset',
                   'select_from', 'with_entities', 'options'):
        getattr(q, method).return_value = q
    q.all.return_value = []
    q.scalar.return_value = 0
    q.first.return_value = None
    q.__iter__ = MagicMock(side_effect=lambda: iter([]))
    q.subquery.return_value = MagicMock()

    sess = MagicMock()
    sess.query.return_value = q
    sess.connection.return_value.execute.return_value.mappings.return_value.fetchall.return_value = []
    sess.execute.return_value.fetchall.return_value = []
    return sess


# ═══════════════════════════════════════════════════════════════════════════
# Base class
# ═══════════════════════════════════════════════════════════════════════════

class StatsApiBase(unittest.TestCase):

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

        self.inst_a = Institution(
            name_uk='Заповідник А', name_en='Reserve A', code='res_a',
            ecoregion_uk='Розточчя', ecoregion_en='Roztochya',
        )
        self.inst_b = Institution(
            name_uk='Заповідник Б', name_en='Reserve B', code='res_b',
        )
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('test').decode('utf-8')

        self.admin = User(username='admin_u', password_hash=pw)
        self.admin.roles.append(r_admin)
        db.session.add(self.admin)

        self.manager = User(username='manager_u', password_hash=pw)
        self.manager.roles.append(r_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.manager)

        self.viewer = User(username='viewer_u', password_hash=pw)
        self.viewer.roles.append(r_viewer)
        db.session.add(self.viewer)

        db.session.commit()

    def _get(self, url, user_id=None, extra_patches=()):
        """GET request with a mocked CT session and extra patches."""
        if user_id:
            _login(self.client, user_id)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch('app.camera_traps.routes.get_ct_session',
                      return_value=_generic_session())
            )
            stack.enter_context(patch('app.camera_traps.routes.close_ct_session'))
            for p in extra_patches:
                stack.enter_context(p)
            return self.client.get(url)

    def _get_json(self, url, user_id=None, extra_patches=()):
        resp = self._get(url, user_id=user_id, extra_patches=extra_patches)
        return resp, json.loads(resp.data)


# ═══════════════════════════════════════════════════════════════════════════
# 1. TOP SPECIES
# ═══════════════════════════════════════════════════════════════════════════

class TestTopSpecies(StatsApiBase):

    URL = '/uk/camera-traps/api/stats/top-species'

    def test_returns_200_with_labels_and_data_keys(self):
        resp, body = self._get_json(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('labels', body)
        self.assertIn('data', body)

    def test_empty_db_returns_empty_lists(self):
        _, body = self._get_json(self.URL)
        self.assertEqual(body['labels'], [])
        self.assertEqual(body['data'], [])

    def test_with_date_params_returns_200(self):
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31'
        resp, _ = self._get_json(url)
        self.assertEqual(resp.status_code, 200)

    def test_with_institution_id_param_returns_200(self):
        url = self.URL + f'?institution_id={self.inst_a.id}'
        resp, body = self._get_json(url, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('labels', body)


# ═══════════════════════════════════════════════════════════════════════════
# 2. STATS LOCATIONS
# ═══════════════════════════════════════════════════════════════════════════

class TestStatsLocations(StatsApiBase):

    URL = '/uk/camera-traps/api/stats/locations'

    def test_returns_200_with_list(self):
        resp, body = self._get_json(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(body, list)

    def test_empty_db_returns_empty_list(self):
        _, body = self._get_json(self.URL)
        self.assertEqual(body, [])

    def test_with_date_params_returns_200(self):
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31'
        resp, _ = self._get_json(url)
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_user_returns_200(self):
        resp, body = self._get_json(self.URL, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(body, list)


# ═══════════════════════════════════════════════════════════════════════════
# 3. DISTRIBUTION MAP
# ═══════════════════════════════════════════════════════════════════════════

class TestDistributionMap(StatsApiBase):

    URL = '/uk/camera-traps/api/stats/distribution-map'

    def test_missing_all_params_returns_400(self):
        resp, body = self._get_json(self.URL)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', body)

    def test_missing_species_id_returns_400(self):
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31'
        resp, _ = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_missing_start_date_returns_400(self):
        url = self.URL + '?species_id=1&end_date=2024-12-31'
        resp, _ = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_missing_end_date_returns_400(self):
        url = self.URL + '?species_id=1&start_date=2024-01-01'
        resp, _ = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_valid_params_no_data_returns_summary_structure(self):
        """With valid params but no data — returns a structure with summary."""
        url = self.URL + '?species_id=1&start_date=2024-01-01&end_date=2024-12-31'
        resp, body = self._get_json(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('summary', body)
        self.assertIn('locations', body)
        self.assertEqual(body['locations'], [])
        self.assertEqual(body['summary']['total_detections'], 0)

    def test_authenticated_user_with_valid_params(self):
        url = self.URL + '?species_id=1&start_date=2024-01-01&end_date=2024-12-31'
        resp, body = self._get_json(url, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('summary', body)


# ═══════════════════════════════════════════════════════════════════════════
# 4. DAILY ACTIVITY
# ═══════════════════════════════════════════════════════════════════════════

class TestDailyActivity(StatsApiBase):

    URL = '/uk/camera-traps/api/stats/daily-activity'

    def test_missing_all_params_returns_400(self):
        resp, body = self._get_json(self.URL)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', body)

    def test_missing_end_date_returns_400(self):
        url = self.URL + '?start_date=2024-01-01&species_ids=1'
        resp, _ = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_missing_species_ids_returns_400(self):
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31'
        resp, body = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_species_id_only_returns_400(self):
        """Only non-numeric species_ids → empty list → 400."""
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31&species_ids=abc'
        resp, body = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_valid_params_insufficient_data_returns_structure(self):
        """Small dataset (< 2 points) — no curve is built, but the structure exists."""
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31&species_ids=1'
        raw_data_patch = patch(
            'app.camera_traps.routes.fetch_raw_daily_data',
            return_value={1: {}},
        )
        effort_patch = patch(
            'app.camera_traps.routes.calculate_total_effort',
            return_value=10,
        )
        resp, body = self._get_json(url, extra_patches=[raw_data_patch, effort_patch])
        self.assertEqual(resp.status_code, 200)
        self.assertIn('total_effort', body)
        self.assertIn('species_data', body)
        self.assertEqual(body['total_effort'], 10)

    def test_result_has_required_keys(self):
        url = self.URL + '?start_date=2024-01-01&end_date=2024-12-31&species_ids=1'
        raw_data_patch = patch(
            'app.camera_traps.routes.fetch_raw_daily_data',
            return_value={},
        )
        effort_patch = patch(
            'app.camera_traps.routes.calculate_total_effort',
            return_value=0,
        )
        resp, body = self._get_json(url, extra_patches=[raw_data_patch, effort_patch])
        self.assertEqual(resp.status_code, 200)
        for key in ('total_effort', 'species_data', 'species_names', 'ci_computed'):
            self.assertIn(key, body)


# ═══════════════════════════════════════════════════════════════════════════
# 5. COMPARISON
# ═══════════════════════════════════════════════════════════════════════════

class TestApiComparison(StatsApiBase):

    URL = '/uk/camera-traps/api/stats/comparison'

    def test_missing_left_scope_returns_400(self):
        url = self.URL + '?right_scope_id=1&right_scope_type=institution'
        resp, body = self._get_json(url)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', body)

    def test_missing_right_scope_returns_400(self):
        url = self.URL + '?left_scope_id=1&left_scope_type=institution'
        resp, body = self._get_json(url)
        self.assertEqual(resp.status_code, 400)

    def test_missing_both_scopes_returns_400(self):
        resp, body = self._get_json(self.URL)
        self.assertEqual(resp.status_code, 400)

    def test_anonymous_cannot_access_institution_scope(self):
        """Anonymous user has no access to any institution → 403."""
        url = (self.URL +
               '?left_scope_id=1&left_scope_type=institution'
               '&right_scope_id=2&right_scope_type=institution')
        resp, body = self._get_json(url)
        self.assertEqual(resp.status_code, 403)
        self.assertIn('error', body)

    def test_viewer_cannot_access_institution_scope(self):
        """Viewer with no institutions has no access → 403."""
        url = (self.URL +
               f'?left_scope_id={self.inst_a.id}&left_scope_type=institution'
               f'&right_scope_id={self.inst_b.id}&right_scope_type=institution')
        resp, body = self._get_json(url, self.viewer.id)
        self.assertEqual(resp.status_code, 403)

    def test_manager_cannot_access_foreign_institution(self):
        """Manager has no access to a foreign institution (inst_b) → 403."""
        url = (self.URL +
               f'?left_scope_id={self.inst_b.id}&left_scope_type=institution'
               f'&right_scope_id={self.inst_b.id}&right_scope_type=institution')
        resp, body = self._get_json(url, self.manager.id)
        self.assertEqual(resp.status_code, 403)

    def test_admin_valid_scopes_no_locations_returns_404(self):
        """Admin, valid scopes, but no locations in the CT DB → 404."""
        url = (self.URL +
               f'?left_scope_id={self.inst_a.id}&left_scope_type=institution'
               f'&right_scope_id={self.inst_b.id}&right_scope_type=institution')
        resp, body = self._get_json(url, self.admin.id)
        self.assertEqual(resp.status_code, 404)

    def test_invalid_date_format_returns_400(self):
        url = (self.URL +
               '?left_scope_id=1&left_scope_type=institution'
               '&right_scope_id=2&right_scope_type=institution'
               '&start_date=not-a-date&end_date=also-not-a-date')
        resp, body = self._get_json(url, self.admin.id)
        self.assertEqual(resp.status_code, 400)


# ═══════════════════════════════════════════════════════════════════════════
# 6. GALLERY PHOTOS
# ═══════════════════════════════════════════════════════════════════════════

class TestGalleryPhotos(StatsApiBase):

    URL = '/uk/camera-traps/api/gallery/photos'

    def test_missing_species_id_returns_400(self):
        resp, body = self._get_json(self.URL)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', body)

    def test_species_id_zero_no_photos_returns_404(self):
        """species_id=0 means "all species", but the DB is empty → 404."""
        resp, body = self._get_json(self.URL + '?species_id=0')
        self.assertEqual(resp.status_code, 404)
        self.assertIn('message', body)

    def test_specific_species_id_no_photos_returns_404(self):
        resp, body = self._get_json(self.URL + '?species_id=1')
        self.assertEqual(resp.status_code, 404)

    def test_authenticated_user_missing_species_id_returns_400(self):
        resp, body = self._get_json(self.URL, self.manager.id)
        self.assertEqual(resp.status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
