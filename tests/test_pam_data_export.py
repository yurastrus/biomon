"""
Tests for the institution filter on the PAM export page
(app/pam/routes.py: pam_data_export, api_data_preview, api_data_download
 + app/pam/utils.py: get_institution_filter).

Structure:
  1. TestGetInstitutionFilter         — admin/manager × single/multi/empty combinations
  2. TestPamDataExportPage            — GET page passes institutions to the template
  3. TestDataPreviewAPI               — POST/GET API parses institution_ids
  4. TestDataDownloadAPI              — same for download

Run:
    venv/Scripts/python -m pytest tests/test_pam_data_export.py -v
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'


# ══════════════════════════════════════════════════════════════════════════════
# 1. get_institution_filter — admin/manager × single/multi/empty combinations
# ══════════════════════════════════════════════════════════════════════════════

class TestGetInstitutionFilter(unittest.TestCase):
    """Pure-function tests — no DB."""

    def test_admin_no_filter_returns_trivial_condition(self):
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(user_inst_ids=[], is_admin=True)
        self.assertEqual(cond.strip(), "1=1")
        self.assertEqual(params, {})

    def test_admin_with_selected_filter_adds_clause(self):
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(
            user_inst_ids=[], is_admin=True, selected_inst_id=[5, 6]
        )
        self.assertIn('AND EXISTS', cond)
        self.assertIn('li_sel.institution_id = ANY(:selected_inst_id)', cond)
        self.assertEqual(params['selected_inst_id'], [5, 6])

    def test_manager_without_filter_uses_only_permission_check(self):
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(user_inst_ids=[1, 2], is_admin=False)
        self.assertIn('li_perm.institution_id = ANY(:user_inst_ids)', cond)
        self.assertNotIn('li_sel', cond)
        self.assertEqual(params, {'user_inst_ids': [1, 2]})

    def test_manager_with_filter_has_both_clauses(self):
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(
            user_inst_ids=[1, 2], is_admin=False, selected_inst_id=[1]
        )
        self.assertIn('li_perm', cond)
        self.assertIn('li_sel', cond)
        self.assertEqual(params['user_inst_ids'], [1, 2])
        self.assertEqual(params['selected_inst_id'], [1])

    def test_anonymous_falls_back_to_visibility_public_only(self):
        """user_inst_ids empty, not admin → public locations only."""
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(user_inst_ids=[], is_admin=False)
        self.assertIn('visibility_level = 0', cond)
        self.assertEqual(params, {})

    def test_selected_string_normalized_to_int_list(self):
        from app.pam.utils import get_institution_filter
        _, params = get_institution_filter(
            user_inst_ids=[1], is_admin=False, selected_inst_id='3,4,5'
        )
        self.assertEqual(params['selected_inst_id'], [3, 4, 5])

    def test_selected_single_int_normalized_to_list(self):
        from app.pam.utils import get_institution_filter
        _, params = get_institution_filter(
            user_inst_ids=[1], is_admin=False, selected_inst_id=7
        )
        self.assertEqual(params['selected_inst_id'], [7])

    def test_selected_empty_list_is_treated_as_no_filter(self):
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(
            user_inst_ids=[1], is_admin=False, selected_inst_id=[]
        )
        # An empty list is treated as falsy → does not add AND EXISTS(li_sel)
        self.assertNotIn('li_sel', cond)
        self.assertNotIn('selected_inst_id', params)

    def test_selected_garbage_string_ignored(self):
        """'abc,def' → no ints → does not add a filter."""
        from app.pam.utils import get_institution_filter
        cond, params = get_institution_filter(
            user_inst_ids=[1], is_admin=False, selected_inst_id='abc,def'
        )
        self.assertNotIn('li_sel', cond)
        self.assertNotIn('selected_inst_id', params)

    def test_selected_mixed_string_keeps_only_digits(self):
        """'1,abc,3' → [1, 3]."""
        from app.pam.utils import get_institution_filter
        _, params = get_institution_filter(
            user_inst_ids=[1], is_admin=False, selected_inst_id='1,abc,3'
        )
        self.assertEqual(params['selected_inst_id'], [1, 3])


# ══════════════════════════════════════════════════════════════════════════════
# Shared Flask test base
# ══════════════════════════════════════════════════════════════════════════════

class _ExportRouteBase(unittest.TestCase):

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

        roles = {n: Role(name=n) for n in ('admin', 'manager', 'viewer')}
        db.session.add_all(roles.values())
        db.session.flush()

        self.inst_a = Institution(name_uk='Парк А', name_en='Park A', code='exp_a')
        self.inst_b = Institution(name_uk='Парк Б', name_en='Park B', code='exp_b')
        self.inst_c = Institution(name_uk='Парк В', name_en='Park C', code='exp_c')
        db.session.add_all([self.inst_a, self.inst_b, self.inst_c])
        db.session.flush()

        pw = bcrypt.generate_password_hash('pass').decode()

        self.admin = User(username='exp_admin', password_hash=pw)
        self.admin.roles.append(roles['admin'])
        db.session.add(self.admin)

        # Manager has only inst_a and inst_b (NOT inst_c)
        self.manager = User(username='exp_manager', password_hash=pw)
        self.manager.roles.append(roles['manager'])
        self.manager.institution_links.extend([
            UserInstitution(institution_id=self.inst_a.id, can_export=True),
            UserInstitution(institution_id=self.inst_b.id, can_export=True),
        ])
        db.session.add(self.manager)

        self.viewer = User(username='exp_viewer', password_hash=pw)
        self.viewer.roles.append(roles['viewer'])
        db.session.add(self.viewer)

        db.session.commit()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True


# ══════════════════════════════════════════════════════════════════════════════
# 2. GET /pam/data-export — page receives institutions in the context
# ══════════════════════════════════════════════════════════════════════════════

class TestPamDataExportPage(_ExportRouteBase):

    def test_anonymous_blocked(self):
        resp = self.client.get('/uk/pam/data-export')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_viewer_blocked(self):
        self._login(self.viewer.id)
        resp = self.client.get('/uk/pam/data-export')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_manager_gets_200(self):
        self._login(self.manager.id)
        resp = self.client.get('/uk/pam/data-export')
        self.assertEqual(resp.status_code, 200)

    def test_manager_sees_only_own_institutions(self):
        """Manager gets only inst_a/inst_b, NOT inst_c."""
        self._login(self.manager.id)
        resp = self.client.get('/uk/pam/data-export')
        html = resp.get_data(as_text=True)
        self.assertIn('Парк А', html)
        self.assertIn('Парк Б', html)
        self.assertNotIn('Парк В', html)

    def test_admin_sees_all_institutions(self):
        self._login(self.admin.id)
        resp = self.client.get('/uk/pam/data-export')
        html = resp.get_data(as_text=True)
        self.assertIn('Парк А', html)
        self.assertIn('Парк Б', html)
        self.assertIn('Парк В', html)

    def test_institution_select_element_present(self):
        self._login(self.manager.id)
        resp = self.client.get('/uk/pam/data-export')
        html = resp.get_data(as_text=True)
        self.assertIn('id="institution-select"', html)
        self.assertIn('multiple', html)

    def test_english_language_uses_english_names(self):
        self._login(self.admin.id)
        resp = self.client.get('/en/pam/data-export')
        html = resp.get_data(as_text=True)
        self.assertIn('Park A', html)
        self.assertIn('Park B', html)


# ══════════════════════════════════════════════════════════════════════════════
# 3. API /api/pam/data-preview — parsing institution_ids
# ══════════════════════════════════════════════════════════════════════════════

class TestDataPreviewAPI(_ExportRouteBase):

    def _stub_get_occurrence_data(self):
        """Patch get_occurrence_data to capture filter args."""
        return patch(
            'app.pam.routes.get_occurrence_data',
            return_value={'data': [], 'total_count': 0}
        )

    def test_institution_ids_parsed_from_querystring(self):
        self._login(self.manager.id)
        with self._stub_get_occurrence_data() as mock_fn:
            self.client.get(
                f'/uk/api/pam/data-preview?institution_ids={self.inst_a.id},{self.inst_b.id}'
            )
        filters = mock_fn.call_args[0][0]
        self.assertEqual(set(filters['institution_ids']),
                         {self.inst_a.id, self.inst_b.id})

    def test_empty_institution_ids_results_in_empty_list(self):
        self._login(self.manager.id)
        with self._stub_get_occurrence_data() as mock_fn:
            self.client.get('/uk/api/pam/data-preview')
        filters = mock_fn.call_args[0][0]
        self.assertEqual(filters['institution_ids'], [])

    def test_garbage_institution_ids_ignored(self):
        """'abc,def' → []."""
        self._login(self.manager.id)
        with self._stub_get_occurrence_data() as mock_fn:
            self.client.get('/uk/api/pam/data-preview?institution_ids=abc,def')
        filters = mock_fn.call_args[0][0]
        self.assertEqual(filters['institution_ids'], [])

    def test_mixed_institution_ids_keeps_only_digits(self):
        self._login(self.manager.id)
        with self._stub_get_occurrence_data() as mock_fn:
            self.client.get('/uk/api/pam/data-preview?institution_ids=1,abc,3')
        filters = mock_fn.call_args[0][0]
        self.assertEqual(filters['institution_ids'], [1, 3])

    def test_anonymous_blocked(self):
        resp = self.client.get('/uk/api/pam/data-preview?institution_ids=1')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_viewer_blocked(self):
        self._login(self.viewer.id)
        resp = self.client.get('/uk/api/pam/data-preview?institution_ids=1')
        self.assertIn(resp.status_code, (302, 401, 403))


# ══════════════════════════════════════════════════════════════════════════════
# 4. API /api/pam/data-download — the same parsing
# ══════════════════════════════════════════════════════════════════════════════

class TestDataDownloadAPI(_ExportRouteBase):

    def _stub_get_occurrence_data(self, data=None):
        return patch(
            'app.pam.routes.get_occurrence_data',
            return_value={'data': data or [], 'total_count': 0}
        )

    def test_institution_ids_parsed(self):
        self._login(self.manager.id)
        with self._stub_get_occurrence_data() as mock_fn:
            self.client.get(f'/uk/api/pam/data-download?institution_ids={self.inst_a.id}')
        filters = mock_fn.call_args[0][0]
        self.assertEqual(filters['institution_ids'], [self.inst_a.id])

    def test_empty_data_returns_404(self):
        self._login(self.manager.id)
        with self._stub_get_occurrence_data(data=[]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertEqual(resp.status_code, 404)

    def test_csv_returned_when_data_present(self):
        self._login(self.manager.id)
        fake_row = {'occurrenceID': 'abc', 'scientificName': 'Test sp'}
        with self._stub_get_occurrence_data(data=[fake_row]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'text/csv')
        body = resp.get_data(as_text=True)
        self.assertIn('occurrenceID', body)
        self.assertIn('Test sp', body)


# ══════════════════════════════════════════════════════════════════════════════
# 5. get_occurrence_data — institution_ids reaches the filter (integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestGetOccurrenceDataFilterPlumbing(_ExportRouteBase):
    """
    Verify that the institution_ids value in filters is actually
    passed to get_institution_filter as selected_inst_id.
    """

    def test_filters_passes_institution_ids_to_inst_filter(self):
        from app.pam.utils import get_occurrence_data
        self._login(self.manager.id)

        with patch('app.pam.utils.get_institution_filter',
                   return_value=("1=1", {})) as mock_filter, \
             patch('app.pam.utils.get_pam_db_connection') as mock_conn:
            # Connection mock — return empty results so flow doesn't crash
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = []
            conn.execute.return_value.fetchone.return_value = (0,)
            mock_conn.return_value = conn

            with self.app.test_request_context('/uk/pam/data-export'):
                # Mimic login_required user
                from flask_login import login_user
                from app.models import User
                login_user(User.query.get(self.manager.id))
                try:
                    get_occurrence_data({
                        'start_date': '2025-01-01',
                        'end_date': '2025-12-31',
                        'institution_ids': [self.inst_a.id, self.inst_b.id],
                    })
                except Exception:
                    pass  # SQL execution will fail with mocks — we just need the filter call

            # The first arg-position call to get_institution_filter
            self.assertTrue(mock_filter.called)
            kw = mock_filter.call_args.kwargs
            self.assertEqual(kw.get('selected_inst_id'),
                             [self.inst_a.id, self.inst_b.id])

    def test_empty_institution_ids_passes_none(self):
        from app.pam.utils import get_occurrence_data
        self._login(self.manager.id)

        with patch('app.pam.utils.get_institution_filter',
                   return_value=("1=1", {})) as mock_filter, \
             patch('app.pam.utils.get_pam_db_connection') as mock_conn:
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = []
            conn.execute.return_value.fetchone.return_value = (0,)
            mock_conn.return_value = conn

            with self.app.test_request_context('/uk/pam/data-export'):
                from flask_login import login_user
                from app.models import User
                login_user(User.query.get(self.manager.id))
                try:
                    get_occurrence_data({
                        'start_date': '2025-01-01',
                        'end_date': '2025-12-31',
                        'institution_ids': [],
                    })
                except Exception:
                    pass

            self.assertTrue(mock_filter.called)
            kw = mock_filter.call_args.kwargs
            # Empty list → falsy → passed as None
            self.assertIsNone(kw.get('selected_inst_id'))


if __name__ == '__main__':
    unittest.main()
