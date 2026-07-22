"""Tests for the scope → AI cascade filter on the /identify page.

Covers:
  1. `get_species_with_ai_predictions` (ai_runner.py) — new scope params
     add the correct WHERE clause and return empty for scopes a non-admin
     user is not allowed to access.
  2. `/api/identify/ai-species` (routes.py) — role-based access, param
     parsing, proxying to the helper function, restrictions for non-admin.

Run:
    venv/Scripts/python -m pytest tests/test_ai_species_cascade.py -v
"""

import contextlib
import os
import unittest
from unittest.mock import patch, MagicMock


# ── helpers ──────────────────────────────────────────────────────────────


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


# ════════════════════════════════════════════════════════════════════════
# 1. UNIT: get_species_with_ai_predictions — scope params
# ════════════════════════════════════════════════════════════════════════


class TestGetSpeciesWithAiPredictionsScope(unittest.TestCase):
    """Verify the new arguments correctly affect the SQL and params.

    Approach: mock `get_ct_session()` so that session.execute(...)
    returns a given list of rows, and query(AIModel).filter_by().first()
    returns a fake active model. Then inspect exactly which SQL
    and params were passed to session.execute.
    """

    def _row(self, sp_id, ua, en, sci, count):
        """Build a row object with attributes like a SQLAlchemy Row."""
        r = MagicMock()
        r.id = sp_id
        r.common_name_ua = ua
        r.common_name_en = en
        r.scientific_name = sci
        r.pending_count = count
        return r

    def _make_mock_session(self, rows=(), eco_inst_ids=None):
        """Build a session.

        eco_inst_ids=None → a single execute (no eco resolution).
        eco_inst_ids=[…]  → two executes: first the eco query, then the main one.
        """
        sess = MagicMock()

        # active model: any .query(...).filter_by(...).first() query
        active = MagicMock(id=42)
        sess.query.return_value.filter_by.return_value.first.return_value = active

        main_result = MagicMock()
        # If rows are tuples — convert them to row mocks.
        row_objs = []
        for row in rows:
            if isinstance(row, tuple):
                row_objs.append(self._row(*row))
            else:
                row_objs.append(row)
        main_result.fetchall.return_value = row_objs

        if eco_inst_ids is not None:
            eco_result = MagicMock()
            eco_result.fetchall.return_value = [(i,) for i in eco_inst_ids]
            sess.execute.side_effect = [eco_result, main_result]
        else:
            sess.execute.return_value = main_result

        return sess

    def _patch_session(self, sess):
        return patch('app.camera_traps.ai_runner.get_ct_session',
                     return_value=sess)

    def test_no_scope_calls_sql_once_without_scope_clause(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        sess = self._make_mock_session(rows=[])
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[10], is_admin=False,
            )
        self.assertEqual(result, [])

        # The first and only execute is the main SQL without scope.
        # (side_effect returns eco_result first, but without scope_eco
        # we don't use it; sess.execute was called once.)
        self.assertEqual(sess.execute.call_count, 1)
        sql_arg = str(sess.execute.call_args.args[0])
        self.assertNotIn('li_sc.institution_id', sql_arg)

    def test_scope_institution_admin_passes_param(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        rows = [(4, 'Козуля', 'Roe Deer', 'Capreolus capreolus', 7)]
        sess = self._make_mock_session(rows=rows)
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[], is_admin=True,
                scope_institution_id=99,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 4)
        self.assertIn('(7)', result[0]['text'])  # count in parentheses

        self.assertEqual(sess.execute.call_count, 1)
        sql_arg = str(sess.execute.call_args.args[0])
        params_arg = sess.execute.call_args.args[1]
        self.assertIn('li_sc.institution_id = :scope_inst_id', sql_arg)
        self.assertEqual(params_arg.get('scope_inst_id'), 99)

    def test_scope_institution_unauthorized_returns_empty(self):
        """Non-admin without access to the institution → [] without hitting SQL."""
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        sess = self._make_mock_session(rows=[])
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[10, 20], is_admin=False,
                scope_institution_id=999,
            )

        self.assertEqual(result, [])
        # There should be no execute — short-circuit before SQL.
        sess.execute.assert_not_called()

    def test_scope_institution_authorized_member_passes(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        rows = [(5, 'Олень', 'Red Deer', 'Cervus elaphus', 3)]
        sess = self._make_mock_session(rows=rows)
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[10, 20], is_admin=False,
                scope_institution_id=20,
            )

        self.assertEqual(len(result), 1)
        params_arg = sess.execute.call_args.args[1]
        self.assertEqual(params_arg.get('scope_inst_id'), 20)

    def test_scope_institution_ids_passes_expanding_param(self):
        """A pre-resolved list of institutions (e.g. an ecoregion expanded by
        the caller) becomes an IN clause — and the helper never queries the
        `institutions` table itself (it lives in the main DB, not ct_db)."""
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        rows = [(4, 'Козуля', 'Roe Deer', 'Capreolus capreolus', 2)]
        sess = self._make_mock_session(rows=rows)

        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[10, 99], is_admin=False,
                scope_institution_ids=[10],
            )

        self.assertEqual(len(result), 1)
        # Exactly one execute — no eco-resolution query against `institutions`.
        self.assertEqual(sess.execute.call_count, 1)
        sql_arg = str(sess.execute.call_args.args[0])
        # Expanding bindparam renders as `IN (__[POSTCOMPILE_scope_inst_ids])`.
        self.assertIn('li_sc.institution_id IN', sql_arg)
        self.assertIn('scope_inst_ids', sql_arg)
        # Regression guard for the ct_db bug: the helper must not touch the
        # institutions table (that lookup belongs to the caller / main DB).
        self.assertNotIn('FROM institutions', sql_arg)
        params_arg = sess.execute.call_args.args[1]
        self.assertEqual(params_arg.get('scope_inst_ids'), (10,))

    def test_scope_institution_ids_empty_returns_empty(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        sess = self._make_mock_session(rows=[])
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[10], is_admin=False,
                scope_institution_ids=[],
            )
        self.assertEqual(result, [])
        # Empty scope → short-circuit before any SQL.
        sess.execute.assert_not_called()

    def test_scope_institution_ids_admin_multiple(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        rows = [(4, 'Козуля', 'Roe Deer', 'Capreolus capreolus', 1)]
        sess = self._make_mock_session(rows=rows)
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[], is_admin=True,
                scope_institution_ids=[10, 20, 30],
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(sess.execute.call_count, 1)
        params_arg = sess.execute.call_args.args[1]
        self.assertEqual(set(params_arg.get('scope_inst_ids')), {10, 20, 30})

    def test_no_model_returns_empty(self):
        # The query no longer depends on the ACTIVE model (it takes the highest
        # accuracy_rank per series), so the short-circuit happens only when the
        # DB has NO model at all: sess.query(AIModel.id).first() → None.
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        sess = MagicMock()
        sess.query.return_value.first.return_value = None
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                user_id=1, user_inst_ids=[10], is_admin=False,
                scope_institution_id=10,
            )
        self.assertEqual(result, [])
        sess.execute.assert_not_called()

    def test_lang_uk_includes_scientific_name(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        rows = [(4, 'Козуля', 'Roe Deer', 'Capreolus capreolus', 12)]
        sess = self._make_mock_session(rows=rows)
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                lang_code='uk', user_id=1, user_inst_ids=[], is_admin=True,
            )
        self.assertEqual(result[0]['text'],
                         'Козуля (Capreolus capreolus) (12)')

    def test_lang_en_uses_english_name(self):
        from app.camera_traps.ai_runner import get_species_with_ai_predictions

        rows = [(4, 'Козуля', 'Roe Deer', 'Capreolus capreolus', 12)]
        sess = self._make_mock_session(rows=rows)
        with self._patch_session(sess):
            result = get_species_with_ai_predictions(
                lang_code='en', user_id=1, user_inst_ids=[], is_admin=True,
            )
        self.assertEqual(result[0]['text'],
                         'Roe Deer (Capreolus capreolus) (12)')


# ════════════════════════════════════════════════════════════════════════
# 2. ROUTE: /api/identify/ai-species
# ════════════════════════════════════════════════════════════════════════


class TestIdentifyAiSpeciesEndpoint(unittest.TestCase):

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
        cls.app.config['WTF_CSRF_ENABLED'] = False

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

        r_admin    = Role(name='admin')
        r_verifier = Role(name='ct_verifier')
        r_viewer   = Role(name='viewer')
        db.session.add_all([r_admin, r_verifier, r_viewer])
        db.session.flush()

        self.inst_a = Institution(
            name_uk='Заповідник А', name_en='Reserve A', code='res_a',
            ecoregion_uk='Карпати', ecoregion_en='Carpathians',
        )
        self.inst_b = Institution(
            name_uk='Заповідник Б', name_en='Reserve B', code='res_b',
            ecoregion_uk='Розточчя', ecoregion_en='Roztochia',
        )
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('test').decode('utf-8')

        self.admin = User(username='ais_admin', password_hash=pw)
        self.admin.roles.append(r_admin)
        db.session.add(self.admin)

        self.verifier = User(username='ais_ver', password_hash=pw)
        self.verifier.roles.append(r_verifier)
        self.verifier.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.verifier)

        self.viewer = User(username='ais_viewer', password_hash=pw)
        self.viewer.roles.append(r_viewer)
        db.session.add(self.viewer)

        db.session.commit()

    URL = '/uk/camera-traps/api/identify/ai-species'

    @contextlib.contextmanager
    def _patched(self, ai_available=True, items_return=None, raise_exc=None):
        """Patch ai_runner functions at the call site (inside routes).

        Note: the route uses `from .ai_runner import ...` — so we patch
        `app.camera_traps.ai_runner.<name>` directly, because the local
        import reads the module attribute on every call.
        """
        sess = MagicMock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch('app.camera_traps.routes.get_ct_session',
                      return_value=sess)
            )
            stack.enter_context(patch('app.camera_traps.routes.close_ct_session'))
            stack.enter_context(
                patch('app.camera_traps.ai_runner.is_ai_available',
                      return_value=ai_available)
            )
            mock_get = MagicMock()
            if raise_exc is not None:
                mock_get.side_effect = raise_exc
            else:
                mock_get.return_value = items_return or []
            stack.enter_context(
                patch('app.camera_traps.ai_runner.get_species_with_ai_predictions',
                      mock_get)
            )
            yield mock_get

    # ── access control ────────────────────────────────────────────

    def test_anonymous_redirected(self):
        with self._patched():
            r = self.client.get(self.URL)
        # role_required redirects (302/303) anonymous users to login
        self.assertIn(r.status_code, (302, 303))

    def test_viewer_forbidden(self):
        _login(self.client, self.viewer.id)
        with self._patched():
            r = self.client.get(self.URL)
        # ct_verifier-only → insufficient permissions. The decorator may either
        # redirect (302) or return 403 — we accept both.
        self.assertIn(r.status_code, (302, 303, 403))

    def test_verifier_ok(self):
        _login(self.client, self.verifier.id)
        with self._patched(items_return=[{'id': 4, 'text': 'Козуля (3)'}]):
            r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['ai_available'])
        self.assertEqual(data['items'], [{'id': 4, 'text': 'Козуля (3)'}])

    # ── ai_available=False ────────────────────────────────────────

    def test_ai_unavailable_returns_empty(self):
        _login(self.client, self.admin.id)
        with self._patched(ai_available=False) as mock_get:
            r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertFalse(data['ai_available'])
        self.assertEqual(data['items'], [])
        mock_get.assert_not_called()

    # ── parsing scope params ──────────────────────────────────────

    def test_admin_with_scope_institution_passes_param(self):
        _login(self.client, self.admin.id)
        with self._patched(items_return=[]) as mock_get:
            r = self.client.get(
                self.URL + f'?scope_institution_id={self.inst_b.id}'
            )
        self.assertEqual(r.status_code, 200)
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs.get('scope_institution_id'), self.inst_b.id)
        self.assertTrue(kwargs.get('is_admin'))

    def test_admin_with_scope_ecoregion_resolves_to_institution_ids(self):
        """The route expands the ecoregion to its institution IDs (main-DB
        Institution model) and passes them as `scope_institution_ids` — it must
        NOT forward a raw `scope_ecoregion` to the CT-only helper."""
        _login(self.client, self.admin.id)
        with self._patched(items_return=[]) as mock_get:
            r = self.client.get(self.URL + '?scope_ecoregion=' + 'Карпати')
        self.assertEqual(r.status_code, 200)
        kwargs = mock_get.call_args.kwargs
        self.assertNotIn('scope_ecoregion', kwargs)
        self.assertEqual(kwargs.get('scope_institution_ids'), [self.inst_a.id])

    def test_admin_scope_ecoregion_unknown_returns_empty_without_helper(self):
        _login(self.client, self.admin.id)
        with self._patched(items_return=[{'id': 1, 'text': 'x'}]) as mock_get:
            r = self.client.get(self.URL + '?scope_ecoregion=' + 'Неіснуючий')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['items'], [])
        mock_get.assert_not_called()

    def test_verifier_scope_ecoregion_intersects_own_institutions(self):
        """A verifier picking their own ecoregion gets their institution(s);
        picking an ecoregion they have no institution in → empty, no helper."""
        _login(self.client, self.verifier.id)
        # verifier is a member of inst_a (ecoregion 'Карпати').
        with self._patched(items_return=[{'id': 4, 'text': 'Козуля (1)'}]) as mock_get:
            r = self.client.get(self.URL + '?scope_ecoregion=' + 'Карпати')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mock_get.call_args.kwargs.get('scope_institution_ids'),
                         [self.inst_a.id])

        # 'Розточчя' belongs to inst_b, which the verifier is NOT a member of.
        with self._patched(items_return=[{'id': 4, 'text': 'nope'}]) as mock_get:
            r = self.client.get(self.URL + '?scope_ecoregion=' + 'Розточчя')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['items'], [])
        mock_get.assert_not_called()

    def test_no_scope_params_pass_none(self):
        _login(self.client, self.verifier.id)
        with self._patched(items_return=[]) as mock_get:
            r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        kwargs = mock_get.call_args.kwargs
        self.assertIsNone(kwargs.get('scope_institution_id'))
        self.assertIsNone(kwargs.get('scope_ecoregion'))

    # ── non-admin cannot peek into another institution ────────────────

    def test_verifier_scope_other_institution_returns_empty(self):
        _login(self.client, self.verifier.id)
        # verifier only has access to inst_a; requests inst_b → []
        with self._patched(items_return=[
            {'id': 4, 'text': 'should not be returned'}
        ]) as mock_get:
            r = self.client.get(
                self.URL + f'?scope_institution_id={self.inst_b.id}'
            )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['items'], [])
        # the helper must not be called — short-circuit in the route
        mock_get.assert_not_called()

    def test_verifier_scope_own_institution_ok(self):
        _login(self.client, self.verifier.id)
        with self._patched(items_return=[
            {'id': 4, 'text': 'Козуля (1)'}
        ]) as mock_get:
            r = self.client.get(
                self.URL + f'?scope_institution_id={self.inst_a.id}'
            )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['items'], [{'id': 4, 'text': 'Козуля (1)'}])
        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs.get('scope_institution_id'), self.inst_a.id)

    # ── exception in helper is swallowed ──────────────────────────

    def test_helper_exception_returns_empty(self):
        _login(self.client, self.admin.id)
        with self._patched(raise_exc=RuntimeError('boom')):
            r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data['ai_available'])
        self.assertEqual(data['items'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
