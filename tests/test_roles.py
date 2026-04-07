"""
Тести для системи ролей: User.has_role()

Запуск:
    venv/Scripts/python -m unittest tests.test_roles -v
"""
import unittest
from unittest.mock import MagicMock


def _make_user(role_names):
    """Мок-юзер із заданими ролями."""
    user = MagicMock()
    user.roles = [MagicMock(name=n) for n in role_names]
    # name у MagicMock за замовчуванням — назва атрибута, задаємо явно
    for role, name in zip(user.roles, role_names):
        role.name = name
    user.is_authenticated = True
    return user


class TestHasRole(unittest.TestCase):
    """Перевірка User.has_role() та ієрархії ролей."""

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _check(self, user_roles, *required):
        from app.models import User
        return User.has_role(_make_user(user_roles), *required)

    # --- admin ---

    def test_admin_passes_any_check(self):
        for role in ('viewer', 'ct_verifier', 'analyst', 'manager', 'pam_verifier', 'nonexistent'):
            with self.subTest(role=role):
                self.assertTrue(self._check(['admin'], role))

    # --- manager ---

    def test_manager_includes_ct_verifier(self):
        self.assertTrue(self._check(['manager'], 'ct_verifier'))

    def test_manager_includes_analyst(self):
        self.assertTrue(self._check(['manager'], 'analyst'))

    def test_manager_includes_pam_verifier(self):
        self.assertTrue(self._check(['manager'], 'pam_verifier'))

    def test_manager_includes_viewer(self):
        self.assertTrue(self._check(['manager'], 'viewer'))

    def test_manager_passes_manager_check(self):
        self.assertTrue(self._check(['manager'], 'manager'))

    # --- analyst ---

    def test_analyst_includes_ct_verifier(self):
        self.assertTrue(self._check(['analyst'], 'ct_verifier'))

    def test_analyst_includes_viewer(self):
        self.assertTrue(self._check(['analyst'], 'viewer'))

    def test_analyst_passes_analyst_check(self):
        self.assertTrue(self._check(['analyst'], 'analyst'))

    def test_analyst_does_not_include_manager(self):
        self.assertFalse(self._check(['analyst'], 'manager'))

    def test_analyst_does_not_include_pam_verifier(self):
        self.assertFalse(self._check(['analyst'], 'pam_verifier'))

    # --- ct_verifier ---

    def test_ct_verifier_includes_viewer(self):
        self.assertTrue(self._check(['ct_verifier'], 'viewer'))

    def test_ct_verifier_passes_ct_verifier_check(self):
        self.assertTrue(self._check(['ct_verifier'], 'ct_verifier'))

    def test_ct_verifier_does_not_include_analyst(self):
        self.assertFalse(self._check(['ct_verifier'], 'analyst'))

    def test_ct_verifier_does_not_include_manager(self):
        self.assertFalse(self._check(['ct_verifier'], 'manager'))

    # --- viewer ---

    def test_viewer_passes_viewer_check(self):
        self.assertTrue(self._check(['viewer'], 'viewer'))

    def test_viewer_does_not_include_ct_verifier(self):
        self.assertFalse(self._check(['viewer'], 'ct_verifier'))

    def test_viewer_does_not_include_analyst(self):
        self.assertFalse(self._check(['viewer'], 'analyst'))

    def test_viewer_does_not_include_manager(self):
        self.assertFalse(self._check(['viewer'], 'manager'))

    # --- edge cases ---

    def test_no_roles_fails_everything(self):
        for role in ('viewer', 'ct_verifier', 'analyst', 'manager', 'admin'):
            with self.subTest(role=role):
                self.assertFalse(self._check([], role))

    def test_multiple_required_any_match_sufficient(self):
        """has_role('a', 'b') → True якщо є хоча б одна."""
        self.assertTrue(self._check(['viewer'], 'viewer', 'ct_verifier'))
        self.assertTrue(self._check(['ct_verifier'], 'viewer', 'ct_verifier'))

    def test_pam_verifier_includes_viewer(self):
        self.assertTrue(self._check(['pam_verifier'], 'viewer'))

    def test_pam_verifier_does_not_include_ct_verifier(self):
        self.assertFalse(self._check(['pam_verifier'], 'ct_verifier'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
