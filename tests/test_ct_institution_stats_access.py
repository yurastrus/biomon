"""
Institution verification stats page: open to every authenticated user, each
within their own access scope.

Covers:
  - GET /camera-traps/institution-stats — anonymous redirects, viewer/verifier
    (previously manager-only) get 200;
  - the route pins a non-admin viewer to their OWN institutions
    (`restrict_inst_ids`), so a shared location cannot leak a co-owner's row,
    while an admin stays unrestricted;
  - the CT landing page shows the institution-stats card to any logged-in user
    and the contributors card to everyone.
"""

import contextlib
import os
import unittest
from unittest.mock import patch, MagicMock


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _generic_session():
    """Self-recursive ct_session mock: every chained method returns itself."""
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
    return sess


class InstitutionStatsBase(unittest.TestCase):

    URL = '/uk/camera-traps/institution-stats'

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

        r_admin = Role(name='admin')
        r_verifier = Role(name='ct_verifier')
        r_viewer = Role(name='viewer')
        db.session.add_all([r_admin, r_verifier, r_viewer])
        db.session.flush()

        self.inst_a = Institution(name_uk='Заповідник А', name_en='Reserve A',
                                  code='res_a')
        self.inst_b = Institution(name_uk='Заповідник Б', name_en='Reserve B',
                                  code='res_b')
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('test').decode('utf-8')

        self.admin = User(username='admin_u', password_hash=pw)
        self.admin.roles.append(r_admin)
        db.session.add(self.admin)

        # Plain verifier with access to exactly ONE institution.
        self.verifier = User(username='ct_verifier_u', password_hash=pw)
        self.verifier.roles.append(r_verifier)
        self.verifier.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_view_ct=True)
        )
        db.session.add(self.verifier)

        # Viewer with no institution at all — public scope only.
        self.viewer = User(username='viewer_u', password_hash=pw)
        self.viewer.roles.append(r_viewer)
        db.session.add(self.viewer)

        db.session.commit()

    def _get(self, url=None, user_id=None, extra_patches=()):
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
            return self.client.get(url or self.URL)


class TestInstitutionStatsAccess(InstitutionStatsBase):

    def test_anonymous_redirects_to_login(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 302)

    def test_plain_verifier_gets_200(self):
        """Was manager-only before; a non-manager user must now get the page."""
        self.assertEqual(self._get(user_id=self.verifier.id).status_code, 200)

    def test_viewer_without_institution_gets_200(self):
        self.assertEqual(self._get(user_id=self.viewer.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(user_id=self.admin.id).status_code, 200)


class TestInstitutionStatsScoping(InstitutionStatsBase):
    """The row-level pin is what keeps a one-institution user to one row."""

    def _captured_restrict(self, user_id):
        spy = MagicMock(return_value=[])
        with patch('app.camera_traps.routes.query_institution_stats', spy):
            resp = self._get(user_id=user_id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(spy.called, 'query_institution_stats не викликано')
        return spy.call_args.kwargs.get('restrict_inst_ids')

    def test_single_institution_user_is_pinned_to_it(self):
        restrict = self._captured_restrict(self.verifier.id)
        self.assertEqual(list(restrict), [self.inst_a.id])
        self.assertNotIn(self.inst_b.id, list(restrict))

    def test_admin_is_not_restricted(self):
        self.assertIsNone(self._captured_restrict(self.admin.id))

    def test_user_without_institutions_is_not_restricted(self):
        """No access scope to pin to — the location filter keeps it public-only."""
        self.assertIsNone(self._captured_restrict(self.viewer.id))


class TestInstitutionStatsRowFilter(InstitutionStatsBase):
    """`restrict_inst_ids` must reach the grouped query, not just the route."""

    def test_query_applies_extra_filter_when_restricted(self):
        from datetime import date
        from sqlalchemy import text
        from app.camera_traps.routes import query_institution_stats

        sess = _generic_session()
        q = sess.query.return_value
        query_institution_stats(sess, date(2026, 9, 3), text('1=1'), {},
                                restrict_inst_ids=[7])
        # 2 access filters (institution condition + valid locations) + the pin.
        self.assertEqual(q.filter.call_count, 3)

    def test_query_adds_no_filter_when_unrestricted(self):
        from datetime import date
        from sqlalchemy import text
        from app.camera_traps.routes import query_institution_stats

        sess = _generic_session()
        q = sess.query.return_value
        query_institution_stats(sess, date(2026, 9, 3), text('1=1'), {},
                                restrict_inst_ids=None)
        self.assertEqual(q.filter.call_count, 2)


class TestHubCards(InstitutionStatsBase):

    HUB = '/uk/camera-traps/'

    def test_logged_in_user_sees_institution_stats_card(self):
        body = self._get(self.HUB, user_id=self.verifier.id).get_data(as_text=True)
        self.assertIn('/camera-traps/institution-stats', body)

    def test_anonymous_does_not_see_institution_stats_card(self):
        body = self._get(self.HUB).get_data(as_text=True)
        self.assertNotIn('/camera-traps/institution-stats', body)

    def test_contributors_card_is_public(self):
        body = self._get(self.HUB).get_data(as_text=True)
        self.assertIn('/camera-traps/contributors', body)

    def test_cards_sit_next_to_each_other(self):
        body = self._get(self.HUB, user_id=self.verifier.id).get_data(as_text=True)
        i_inst = body.find('/camera-traps/institution-stats')
        i_contrib = body.find('/camera-traps/contributors')
        self.assertNotEqual(i_inst, -1)
        self.assertNotEqual(i_contrib, -1)
        # Adjacent cards: only the institution card's own markup between them.
        self.assertLess(i_contrib - i_inst, 800)


if __name__ == '__main__':
    unittest.main()
