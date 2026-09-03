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

        roles = {n: Role(name=n)
                 for n in ('admin', 'manager', 'analyst', 'pam_verifier', 'viewer')}
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

        # Manager: can_export on inst_a/inst_b, member of inst_c WITHOUT export.
        # inst_c pins the rule that membership alone grants nothing.
        self.manager = User(username='exp_manager', password_hash=pw)
        self.manager.roles.append(roles['manager'])
        self.manager.institution_links.extend([
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True, can_export_ct=True, can_view_pam=True, can_export_pam=True),
            UserInstitution(institution_id=self.inst_b.id, can_view_ct=True, can_export_ct=True, can_view_pam=True, can_export_pam=True),
            UserInstitution(institution_id=self.inst_c.id, can_view_ct=True, can_view_pam=True),
        ])
        db.session.add(self.manager)

        # Analyst with export rights on inst_a only — the vasylyna case.
        self.analyst = User(username='exp_analyst', password_hash=pw)
        self.analyst.roles.extend([roles['analyst'], roles['pam_verifier']])
        self.analyst.institution_links.extend([
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True, can_export_ct=True, can_view_pam=True, can_export_pam=True),
            UserInstitution(institution_id=self.inst_b.id, can_view_ct=True, can_view_pam=True),
        ])
        db.session.add(self.analyst)

        # Analyst role but can_export nowhere → must stay blocked.
        self.analyst_noexport = User(username='exp_analyst_noexp', password_hash=pw)
        self.analyst_noexport.roles.append(roles['analyst'])
        self.analyst_noexport.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True, can_view_pam=True)
        )
        db.session.add(self.analyst_noexport)

        # pam_verifier only — below the analyst level, no export.
        self.verifier = User(username='exp_verifier', password_hash=pw)
        self.verifier.roles.append(roles['pam_verifier'])
        self.verifier.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True, can_export_ct=True, can_view_pam=True, can_export_pam=True)
        )
        db.session.add(self.verifier)

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

    def test_manager_sees_only_export_flagged_institutions(self):
        """Manager gets inst_a/inst_b (PAM export granted) but NOT inst_c,
        where they have access without the PAM export flag."""
        self._login(self.manager.id)
        resp = self.client.get('/uk/pam/data-export')
        html = resp.get_data(as_text=True)
        self.assertIn('Парк А', html)
        self.assertIn('Парк Б', html)
        self.assertNotIn('Парк В', html)

    def test_analyst_with_export_rights_gets_200(self):
        self._login(self.analyst.id)
        resp = self.client.get('/uk/pam/data-export')
        self.assertEqual(resp.status_code, 200)

    def test_analyst_sees_only_export_flagged_institutions(self):
        """Analyst has can_export on inst_a only — inst_b membership is not enough."""
        self._login(self.analyst.id)
        resp = self.client.get('/uk/pam/data-export')
        html = resp.get_data(as_text=True)
        self.assertIn('Парк А', html)
        self.assertNotIn('Парк Б', html)
        self.assertNotIn('Парк В', html)

    def test_analyst_without_export_flag_blocked(self):
        """Analyst role but can_export nowhere → redirected off the page."""
        self._login(self.analyst_noexport.id)
        resp = self.client.get('/uk/pam/data-export')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_pam_verifier_blocked(self):
        """pam_verifier sits below analyst in the hierarchy — no export."""
        self._login(self.verifier.id)
        resp = self.client.get('/uk/pam/data-export')
        self.assertIn(resp.status_code, (302, 401, 403))

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

    def test_analyst_with_export_rights_allowed(self):
        self._login(self.analyst.id)
        with self._stub_get_occurrence_data():
            resp = self.client.get('/uk/api/pam/data-preview')
        self.assertEqual(resp.status_code, 200)

    def test_analyst_without_export_flag_blocked(self):
        self._login(self.analyst_noexport.id)
        with self._stub_get_occurrence_data():
            resp = self.client.get('/uk/api/pam/data-preview')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_pam_verifier_blocked(self):
        self._login(self.verifier.id)
        with self._stub_get_occurrence_data():
            resp = self.client.get('/uk/api/pam/data-preview')
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

    # --- authorisation (the endpoint used to carry no decorators at all) ---

    def test_anonymous_blocked(self):
        fake_row = {'occurrenceID': 'abc'}
        with self._stub_get_occurrence_data(data=[fake_row]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertIn(resp.status_code, (302, 401, 403))
        self.assertNotEqual(resp.mimetype, 'text/csv')

    def test_viewer_blocked(self):
        self._login(self.viewer.id)
        fake_row = {'occurrenceID': 'abc'}
        with self._stub_get_occurrence_data(data=[fake_row]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertIn(resp.status_code, (302, 401, 403))
        self.assertNotEqual(resp.mimetype, 'text/csv')

    def test_pam_verifier_blocked(self):
        self._login(self.verifier.id)
        with self._stub_get_occurrence_data(data=[{'occurrenceID': 'abc'}]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_analyst_without_export_flag_blocked(self):
        self._login(self.analyst_noexport.id)
        with self._stub_get_occurrence_data(data=[{'occurrenceID': 'abc'}]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_analyst_with_export_rights_gets_csv(self):
        self._login(self.analyst.id)
        with self._stub_get_occurrence_data(data=[{'occurrenceID': 'abc'}]):
            resp = self.client.get('/uk/api/pam/data-download')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'text/csv')


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


# ══════════════════════════════════════════════════════════════════════════════
# 6. get_occurrence_data — access baseline is can_export, not membership
# ══════════════════════════════════════════════════════════════════════════════

class TestOccurrenceDataExportScope(_ExportRouteBase):

    def _call_as(self, user_id, filters=None):
        """Run get_occurrence_data as user_id, returning the captured
        get_institution_filter call plus the function's own result."""
        from app.pam.utils import get_occurrence_data
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
                login_user(User.query.get(user_id))
                result = None
                try:
                    result = get_occurrence_data({
                        'start_date': '2025-01-01',
                        'end_date': '2025-12-31',
                        **(filters or {}),
                    })
                except Exception:
                    pass  # SQL fails under mocks; we only inspect the filter call
            return mock_filter, result

    def test_baseline_uses_export_institutions_only(self):
        """Manager is a member of inst_c but without can_export → inst_c must
        not appear in the access baseline."""
        mock_filter, _ = self._call_as(self.manager.id)
        self.assertTrue(mock_filter.called)
        user_inst_ids = mock_filter.call_args[0][0]
        self.assertEqual(set(user_inst_ids), {self.inst_a.id, self.inst_b.id})
        self.assertNotIn(self.inst_c.id, user_inst_ids)

    def test_analyst_baseline_limited_to_flagged_institution(self):
        mock_filter, _ = self._call_as(self.analyst.id)
        user_inst_ids = mock_filter.call_args[0][0]
        self.assertEqual(list(user_inst_ids), [self.inst_a.id])

    def test_no_export_rights_returns_empty_without_querying(self):
        """A non-admin with can_export nowhere gets nothing — it must NOT fall
        back to public (visibility_level = 0) locations."""
        mock_filter, result = self._call_as(self.analyst_noexport.id)
        self.assertEqual(result, {'data': [], 'total_count': 0})
        self.assertFalse(mock_filter.called)

    def test_admin_baseline_is_admin_flag(self):
        mock_filter, _ = self._call_as(self.admin.id)
        self.assertTrue(mock_filter.called)
        self.assertTrue(mock_filter.call_args[0][1])  # is_admin


# ══════════════════════════════════════════════════════════════════════════════
# 7. PAM hub — export card visibility
# ══════════════════════════════════════════════════════════════════════════════

class TestPamHomeExportCard(_ExportRouteBase):

    EXPORT_HREF = '/pam/data-export'

    def _hub_html(self, user_id=None):
        if user_id is not None:
            self._login(user_id)
        return self.client.get('/uk/pam').get_data(as_text=True)

    def test_card_hidden_for_anonymous(self):
        self.assertNotIn(self.EXPORT_HREF, self._hub_html())

    def test_card_hidden_for_viewer(self):
        self.assertNotIn(self.EXPORT_HREF, self._hub_html(self.viewer.id))

    def test_card_hidden_for_pam_verifier(self):
        self.assertNotIn(self.EXPORT_HREF, self._hub_html(self.verifier.id))

    def test_card_hidden_for_analyst_without_export_flag(self):
        self.assertNotIn(self.EXPORT_HREF, self._hub_html(self.analyst_noexport.id))

    def test_card_visible_for_analyst_with_export_flag(self):
        """The regression this whole change is about."""
        self.assertIn(self.EXPORT_HREF, self._hub_html(self.analyst.id))

    def test_card_visible_for_manager(self):
        self.assertIn(self.EXPORT_HREF, self._hub_html(self.manager.id))

    def test_card_visible_for_admin(self):
        self.assertIn(self.EXPORT_HREF, self._hub_html(self.admin.id))


if __name__ == '__main__':
    unittest.main()
