"""
Тести доступу до основних сторінок модуля camera_traps.

Покриває:
  - GET /dashboard                   — публічна (анонімний + всі ролі)
  - GET /analysis/species-dashboard  — публічна
  - GET /analysis/species-detailed   — публічна
  - GET /analysis/comparison         — публічна
  - GET /analysis/daily-activity     — публічна
  - GET /gallery                     — публічна
  - GET /upload                      — тільки manager+ (redirect для решти)
  - GET /identify                    — тільки ct_verifier+ (redirect для решти)

Запуск:
    venv/Scripts/python -m unittest tests.test_ct_pages_access -v
"""

import contextlib
import os
import unittest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════
# Допоміжні функції
# ═══════════════════════════════════════════════════════════════════════════

def _login(client, user_id):
    """Встановлює Flask-Login сесію без HTTP-запиту."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _generic_session():
    """
    Mock ct_session: будь-який ORM-ланцюжок повертає [], scalar() → 0.

    Реалізація через само-рекурсивний mock q: кожен метод (join, filter, ...)
    повертає той самий об'єкт q, тому .all() та .scalar() завжди досяжні
    незалежно від довжини ланцюжка.
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
# Базовий клас
# ═══════════════════════════════════════════════════════════════════════════

class PageAccessBase(unittest.TestCase):

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

        r_admin    = Role(name='admin')
        r_manager  = Role(name='manager')
        r_verifier = Role(name='ct_verifier')
        r_viewer   = Role(name='viewer')
        db.session.add_all([r_admin, r_manager, r_verifier, r_viewer])
        db.session.flush()

        self.inst_a = Institution(
            name_uk='Заповідник А', name_en='Reserve A', code='res_a',
        )
        db.session.add(self.inst_a)
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

        self.ct_verifier = User(username='ct_verifier_u', password_hash=pw)
        self.ct_verifier.roles.append(r_verifier)
        db.session.add(self.ct_verifier)

        self.viewer = User(username='viewer_u', password_hash=pw)
        self.viewer.roles.append(r_viewer)
        db.session.add(self.viewer)

        db.session.commit()

    def _get(self, url, user_id=None, extra_patches=()):
        """GET-запит з замоканою CT-сесією та додатковими патчами."""
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


# ═══════════════════════════════════════════════════════════════════════════
# 1. DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

class TestDashboardAccess(PageAccessBase):

    URL = '/uk/camera-traps/dashboard'

    def test_anonymous_gets_200(self):
        self.assertEqual(self._get(self.URL).status_code, 200)

    def test_viewer_gets_200(self):
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 200)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(self.URL, self.admin.id).status_code, 200)

    def test_root_url_gets_200(self):
        self.assertEqual(self._get('/uk/camera-traps/').status_code, 200)

    def test_english_url_gets_200(self):
        self.assertEqual(self._get('/en/camera-traps/dashboard').status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 2. SPECIES DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

class TestSpeciesDashboardAccess(PageAccessBase):

    URL = '/uk/camera-traps/analysis/species-dashboard'

    def test_anonymous_gets_200(self):
        self.assertEqual(self._get(self.URL).status_code, 200)

    def test_viewer_gets_200(self):
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 200)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(self.URL, self.admin.id).status_code, 200)

    def test_english_url_gets_200(self):
        self.assertEqual(self._get('/en/camera-traps/analysis/species-dashboard').status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 3. SPECIES DETAILED
# ═══════════════════════════════════════════════════════════════════════════

class TestSpeciesDetailedAccess(PageAccessBase):

    URL = '/uk/camera-traps/analysis/species-detailed'

    def test_anonymous_gets_200(self):
        self.assertEqual(self._get(self.URL).status_code, 200)

    def test_viewer_gets_200(self):
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 200)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 4. COMPARISON DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

class TestComparisonAccess(PageAccessBase):

    URL = '/uk/camera-traps/analysis/comparison'

    def test_anonymous_gets_200(self):
        self.assertEqual(self._get(self.URL).status_code, 200)

    def test_viewer_gets_200(self):
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 200)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(self.URL, self.admin.id).status_code, 200)

    def test_admin_sees_institution_in_page(self):
        """Адмін бачить всі установи в фільтрі сторінки порівняння."""
        resp = self._get(self.URL, self.admin.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Заповідник А'.encode(), resp.data)


# ═══════════════════════════════════════════════════════════════════════════
# 5. DAILY ACTIVITY PAGE
# ═══════════════════════════════════════════════════════════════════════════

class TestDailyActivityAccess(PageAccessBase):

    URL = '/uk/camera-traps/analysis/daily-activity'

    def _species_patch(self):
        return patch(
            'app.camera_traps.routes.get_cached_species_for_filter',
            return_value=[],
        )

    def test_anonymous_gets_200(self):
        resp = self._get(self.URL, extra_patches=[self._species_patch()])
        self.assertEqual(resp.status_code, 200)

    def test_viewer_gets_200(self):
        resp = self._get(self.URL, self.viewer.id, [self._species_patch()])
        self.assertEqual(resp.status_code, 200)

    def test_manager_gets_200(self):
        resp = self._get(self.URL, self.manager.id, [self._species_patch()])
        self.assertEqual(resp.status_code, 200)

    def test_english_url_gets_200(self):
        resp = self._get(
            '/en/camera-traps/analysis/daily-activity',
            extra_patches=[self._species_patch()],
        )
        self.assertEqual(resp.status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 6. GALLERY
# ═══════════════════════════════════════════════════════════════════════════

class TestGalleryAccess(PageAccessBase):

    URL = '/uk/camera-traps/gallery'

    def test_anonymous_gets_200(self):
        self.assertEqual(self._get(self.URL).status_code, 200)

    def test_viewer_gets_200(self):
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 200)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(self.URL, self.admin.id).status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 7. UPLOAD — тільки manager+
# ═══════════════════════════════════════════════════════════════════════════

class TestUploadAccess(PageAccessBase):

    URL = '/uk/camera-traps/upload'

    def test_anonymous_redirects(self):
        """Незалогінений користувач отримує редирект (не 200)."""
        self.assertEqual(self._get(self.URL).status_code, 302)

    def test_viewer_redirects(self):
        """Viewer не має права — редирект на dashboard."""
        self.assertEqual(self._get(self.URL, self.viewer.id).status_code, 302)

    def test_ct_verifier_redirects(self):
        """ct_verifier нижче manager у ієрархії — редирект."""
        self.assertEqual(self._get(self.URL, self.ct_verifier.id).status_code, 302)

    def test_manager_gets_200(self):
        self.assertEqual(self._get(self.URL, self.manager.id).status_code, 200)

    def test_admin_gets_200(self):
        self.assertEqual(self._get(self.URL, self.admin.id).status_code, 200)


# ═══════════════════════════════════════════════════════════════════════════
# 8. IDENTIFY — тільки ct_verifier+
# ═══════════════════════════════════════════════════════════════════════════

class TestIdentifyAccess(PageAccessBase):

    URL = '/uk/camera-traps/identify'

    def _ranking_patch(self):
        return patch('app.camera_traps.routes.get_species_ranking', return_value={})

    def test_anonymous_redirects(self):
        """Незалогінений користувач отримує редирект."""
        resp = self._get(self.URL, extra_patches=[self._ranking_patch()])
        self.assertEqual(resp.status_code, 302)

    def test_viewer_redirects(self):
        """Viewer нижче ct_verifier — редирект."""
        resp = self._get(self.URL, self.viewer.id, [self._ranking_patch()])
        self.assertEqual(resp.status_code, 302)

    def test_ct_verifier_gets_200(self):
        resp = self._get(self.URL, self.ct_verifier.id, [self._ranking_patch()])
        self.assertEqual(resp.status_code, 200)

    def test_manager_gets_200(self):
        """manager вище ct_verifier — доступ є."""
        resp = self._get(self.URL, self.manager.id, [self._ranking_patch()])
        self.assertEqual(resp.status_code, 200)

    def test_admin_gets_200(self):
        resp = self._get(self.URL, self.admin.id, [self._ranking_patch()])
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
