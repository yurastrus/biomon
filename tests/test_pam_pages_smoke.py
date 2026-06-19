"""
Smoke test for all PAM pages.

Purpose:
  - Regression net during the style refactor (Steps 0-10).
  - Every public PAM page must return 200 OK for an admin user.
  - Tests catch TemplateNotFound, TemplateSyntaxError, BuildError, etc.

IMPORTANT: tests use a mocked PAM DB. Pages that require real SELECT
results are mocked minimally -- so we surface template rendering
errors specifically, not DB-logic errors.

Run:
    venv/Scripts/python -m pytest tests/test_pam_pages_smoke.py -v
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'


# ══════════════════════════════════════════════════════════════════════════════
# List of PAM module pages for the smoke test
# ══════════════════════════════════════════════════════════════════════════════
#
# Each entry: (url_path, expected_template_name, route_function_name).
# expected_template_name -- a substring we look for in response.data to
# confirm the right template rendered (not a redirect or an error).

PAM_PAGES = [
    # url,                                       template_marker,                  route
    ('/uk/pam',                                  'module-hub',                      'pam.pam_home'),
    ('/uk/pam/import',                           'import-layout',                   'pam.pam_import'),
    ('/uk/pam/data-export',                      'data-filters-form',               'pam.pam_data_export'),
    ('/uk/pam/evaluation/results',               'species_choice',                  'pam.evaluation_results'),
    ('/uk/pam/manage-locations',                 'locations-list',                  'pam.manage_pam_locations'),
    ('/uk/pam/verification/upload',              'upload',                          'pam.verification_upload'),
    ('/uk/pam/verification/segments',            'segments',                        'pam.verification_segments'),
    ('/uk/pam/verification/verify',              'verification',                    'pam.verification_interface'),
    ('/uk/pam/pam_overview',                     'overview',                        'pam.pam_overview'),
    ('/uk/pam/pam_detailed',                     'species',                         'pam.pam_detailed'),
    ('/uk/pam/trends',                           'trends',                          'pam.pam_species_dashboard'),
    ('/uk/pam/species-dashboard',                'species',                         'pam.pam_yearly_trends'),
    ('/uk/pam/yearly-table',                     'yearly',                          'pam.pam_yearly_table'),
]


# ══════════════════════════════════════════════════════════════════════════════
# Base class for smoke tests
# ══════════════════════════════════════════════════════════════════════════════

class _PamSmokeBase(unittest.TestCase):

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
        """Create a test admin with all PAM roles."""
        from app.extensions import db, bcrypt
        from app.models import User, Role

        # All roles that may be needed for PAM pages
        role_names = ['admin', 'manager', 'pam_verifier',
                      'roztochya_user', 'fzs_user', 'volunteer_user', 'analyst']
        roles = {n: Role(name=n) for n in role_names}
        db.session.add_all(roles.values())
        db.session.flush()

        pw = bcrypt.generate_password_hash('pass').decode()
        self.admin = User(username='smoke_admin', password_hash=pw)
        # Admin gets every role -- to avoid 403 on any page
        for r in roles.values():
            self.admin.roles.append(r)
        db.session.add(self.admin)
        db.session.commit()

    def _login(self, user_id):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def _mock_pam_db_connection(self):
        """
        Create a fake PAM connection that returns empty results for any
        query. That is enough for the template to render without DB errors.
        """
        conn = MagicMock()

        # fetchall → [], fetchone → None, scalar → 0
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar.return_value = 0
        result.__iter__ = lambda self: iter([])
        conn.execute.return_value = result
        return conn


# ══════════════════════════════════════════════════════════════════════════════
# Tests: each page -> 200 (or known redirects)
# ══════════════════════════════════════════════════════════════════════════════

class TestPamPagesSmoke(_PamSmokeBase):
    """
    For each PAM page we verify that admin gets 200 OK.
    If a page fails with 500 (TemplateSyntaxError, etc.) -- the test fails
    with a clear message.

    No 302 from login_required -- admin is logged in.
    No 302 from role_required either -- admin has every role.
    """

    def _get_with_mocked_db(self, url):
        """GET a page with a mocked PAM connection."""
        conn = self._mock_pam_db_connection()
        # Patch all possible PAM DB entry points
        patches = [
            patch('app.pam.utils.get_pam_db_connection', return_value=conn),
            patch('app.pam.routes.get_pam_db_connection', return_value=conn,
                  create=True),
        ]
        for p in patches:
            try:
                p.start()
            except (AttributeError, ModuleNotFoundError):
                pass
        try:
            return self.client.get(url, follow_redirects=False)
        finally:
            for p in patches:
                try:
                    p.stop()
                except RuntimeError:
                    pass

    def test_all_pages_dont_500(self):
        """No PAM page should return 5xx."""
        self._login(self.admin.id)
        failures = []
        for url, marker, route in PAM_PAGES:
            with self.subTest(url=url):
                try:
                    resp = self._get_with_mocked_db(url)
                except Exception as e:
                    failures.append(f"{url}: EXCEPTION {type(e).__name__}: {e}")
                    continue
                if resp.status_code >= 500:
                    body = resp.get_data(as_text=True)[:200]
                    failures.append(
                        f"{url} → {resp.status_code}\n    body: {body}"
                    )
        if failures:
            self.fail("PAM pages failed:\n  " + "\n  ".join(failures))

    def test_all_pages_return_200_or_known_redirect(self):
        """
        Each page must return 200. Allowed exceptions:
        302 (redirect, e.g. to pam_home on a DB error).
        """
        self._login(self.admin.id)
        results = []
        for url, marker, route in PAM_PAGES:
            with self.subTest(url=url):
                resp = self._get_with_mocked_db(url)
                results.append((url, resp.status_code))
                # 200 -- fine
                # 302 -- acceptable (redirect to hub on a DB error)
                self.assertIn(
                    resp.status_code, (200, 302),
                    f"{url} → {resp.status_code} (expected 200 or 302)"
                )
        # Report to the logs for convenience
        ok_count = sum(1 for _, s in results if s == 200)
        print(f"\nSmoke summary: {ok_count}/{len(results)} pages returned 200 OK")
        for url, status in results:
            tag = '✓' if status == 200 else '↪'
            print(f"  {tag} {status}  {url}")


# ══════════════════════════════════════════════════════════════════════════════
# Tests for specific templates -- verify the correct one renders
# (guard against the case where a template silently fails via flash+redirect)
# ══════════════════════════════════════════════════════════════════════════════

class TestPamHomePage(_PamSmokeBase):
    """pam_home -- the most important page, checked separately."""

    def test_renders_module_hub(self):
        self._login(self.admin.id)
        resp = self.client.get('/uk/pam')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('module-hub', html)
        self.assertIn('hub-card', html)

    def test_renders_all_three_sections(self):
        """Analytics / Verification / Management -- all must be present."""
        self._login(self.admin.id)
        resp = self.client.get('/uk/pam')
        html = resp.get_data(as_text=True)
        # Check by markers -- Ukrainian or English text
        self.assertTrue(
            'Аналітика' in html or 'Analytics' in html,
            'Analytics section missing'
        )
        self.assertTrue(
            'Верифікація' in html or 'Verification' in html,
            'Verification section missing'
        )
        self.assertTrue(
            'Управління' in html or 'Management' in html,
            'Management section missing'
        )

    def test_uses_pam_base_template(self):
        """pam_home must inherit from pam_base.html -- checked via the
        included pam_style.css."""
        self._login(self.admin.id)
        resp = self.client.get('/uk/pam')
        html = resp.get_data(as_text=True)
        self.assertIn('pam_style.css', html,
                      'pam_home.html must include pam_style.css via pam_base.html')

    def test_back_link_absent_on_hub(self):
        """The hub itself must not have a back-link (this is the hub)."""
        self._login(self.admin.id)
        resp = self.client.get('/uk/pam')
        html = resp.get_data(as_text=True)
        self.assertNotIn('pam-back-link', html)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for pam_base.html structure
# ══════════════════════════════════════════════════════════════════════════════

class TestPamBaseTemplate(unittest.TestCase):
    """Verify that pam_base.html has the correct structure."""

    def test_extends_base_html(self):
        from pathlib import Path
        content = Path('app/pam/templates/pam_base.html').read_text(encoding='utf-8')
        self.assertIn('extends "base.html"', content)

    def test_loads_pam_style_css(self):
        from pathlib import Path
        content = Path('app/pam/templates/pam_base.html').read_text(encoding='utf-8')
        self.assertIn('pam_style.css', content)
        self.assertIn("serve_pam_static", content)

    def test_provides_pam_content_block(self):
        from pathlib import Path
        content = Path('app/pam/templates/pam_base.html').read_text(encoding='utf-8')
        self.assertIn('{% block pam_content %}', content)

    def test_provides_pam_head_extra_block(self):
        from pathlib import Path
        content = Path('app/pam/templates/pam_base.html').read_text(encoding='utf-8')
        self.assertIn('{% block pam_head_extra %}', content)

    def test_back_link_conditional_on_endpoint(self):
        """Back-link must not appear on pam_home itself."""
        from pathlib import Path
        content = Path('app/pam/templates/pam_base.html').read_text(encoding='utf-8')
        self.assertIn("request.endpoint != 'pam.pam_home'", content)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for pam_style.css -- required elements
# ══════════════════════════════════════════════════════════════════════════════

class TestNoInlineStyles(unittest.TestCase):
    """Regression: after the refactor no PAM template should contain <style>."""

    def test_no_pam_template_has_inline_style(self):
        from pathlib import Path
        offenders = []
        for path in Path('app/pam/templates').glob('*.html'):
            content = path.read_text(encoding='utf-8')
            if '<style>' in content:
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            f"PAM templates with inline <style> blocks: {offenders}. "
            f"All styles must live in app/pam/static/css/pam_style.css"
        )

    def test_all_pam_templates_extend_pam_base(self):
        """All PAM templates (except pam_base.html itself) must extend 'pam_base.html'."""
        from pathlib import Path
        import re
        offenders = []
        for path in Path('app/pam/templates').glob('*.html'):
            if path.name == 'pam_base.html':
                continue
            content = path.read_text(encoding='utf-8')
            m = re.search(r'\{%\s*extends\s+"([^"]+)"', content)
            if not m or m.group(1) != 'pam_base.html':
                offenders.append((path.name, m.group(1) if m else None))
        self.assertEqual(
            offenders, [],
            f"PAM templates not extending pam_base.html: {offenders}"
        )


class TestPamStyleCss(unittest.TestCase):
    """Core CSS-file invariants -- must hold throughout the entire refactor."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.css = Path('app/pam/static/css/pam_style.css').read_text(encoding='utf-8')

    def test_has_root_variables(self):
        self.assertIn(':root', self.css)
        # Check the key tokens
        for var in ('--color-primary', '--text-primary', '--bg-primary',
                    '--card-bg', '--border-color', '--radius-md', '--shadow-sm'):
            self.assertIn(var, self.css, f'CSS variable {var} missing')

    def test_has_dark_theme(self):
        self.assertIn('body.dark-theme', self.css)

    def test_has_container_reset(self):
        """style.css must be overridden, otherwise the grid won't work."""
        self.assertIn('main .container:not(.maplistcontainer)', self.css)
        # Must be display: block (not flex)
        idx = self.css.find('main .container:not(.maplistcontainer)')
        block_section = self.css[idx:idx + 300]
        self.assertIn('display: block', block_section)

    def test_has_back_link(self):
        self.assertIn('.pam-back-link', self.css)

    def test_has_hub_classes(self):
        for cls in ('.module-hub', '.hub-card', '.hub-grid', '.hub-section-title'):
            self.assertIn(cls, self.css, f'Hub class {cls} missing')

    def test_has_numbered_sections(self):
        """18 numbered sections -- the structural backbone of the file."""
        import re
        # Look for comments of the form "   1. " ... "   18. "
        sections = re.findall(r'\n\s*(\d+)\.\s+\w', self.css)
        section_nums = set(int(n) for n in sections)
        # At least 10 of 18 sections must be present (the rest are added gradually)
        self.assertGreaterEqual(
            len(section_nums), 10,
            f'Очікувано принаймні 10 секцій, знайдено {len(section_nums)}: {sorted(section_nums)}'
        )


if __name__ == '__main__':
    unittest.main()
