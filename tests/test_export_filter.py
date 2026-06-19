"""
Tests for _get_export_institution_ids() (app/camera_traps/routes.py).

The function determines which institution_ids are allowed for a request:
- admin: may specify any, or gets None (no restrictions)
- others: only the intersection with their own institutions

Run:
    venv/Scripts/python -m unittest tests.test_export_filter -v
"""
import unittest
from unittest.mock import MagicMock, patch


def _make_user(role_names, inst_ids):
    from app.models import User
    user = MagicMock()
    user.roles = [MagicMock(name=n) for n in role_names]
    for role, name in zip(user.roles, role_names):
        role.name = name
    user.institutions = [MagicMock(id=i) for i in inst_ids]
    user.export_institutions = [MagicMock(id=i) for i in inst_ids]
    user.is_authenticated = True
    # Wire up the real has_role logic instead of the default MagicMock
    user.has_role.side_effect = lambda *args: User.has_role(user, *args)
    return user


def _call(app, user, institution_ids_param=''):
    """Calls _get_export_institution_ids() in the correct Flask context."""
    from app.camera_traps.routes import _get_export_institution_ids
    url = f'/uk/camera-traps/api/data-download'
    if institution_ids_param:
        url += f'?institution_ids={institution_ids_param}'
    with app.test_request_context(url):
        with patch('app.camera_traps.routes.current_user', user):
            return _get_export_institution_ids()


class TestGetExportInstitutionIds(unittest.TestCase):

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    # --- admin ---

    def test_admin_no_param_returns_none(self):
        """Admin without a param — None (no restriction)."""
        user = _make_user(['admin'], [1, 2])
        result = _call(self.app, user, '')
        self.assertIsNone(result)

    def test_admin_with_param_returns_requested(self):
        """Admin with a param — returns the requested IDs."""
        user = _make_user(['admin'], [1])
        result = _call(self.app, user, '5,10,20')
        self.assertEqual(sorted(result), [5, 10, 20])

    def test_admin_empty_param_after_comma_ignored(self):
        """Empty parts in the param are ignored."""
        user = _make_user(['admin'], [])
        result = _call(self.app, user, '3,,7')
        self.assertEqual(sorted(result), [3, 7])

    # --- non-admin, no param ---

    def test_non_admin_no_param_returns_own_institutions(self):
        """Without a param — returns all own institutions."""
        user = _make_user(['analyst'], [10, 20, 30])
        result = _call(self.app, user, '')
        self.assertEqual(sorted(result), [10, 20, 30])

    def test_non_admin_no_institutions_returns_empty(self):
        """A user with no institutions gets an empty list."""
        user = _make_user(['analyst'], [])
        result = _call(self.app, user, '')
        self.assertEqual(result, [])

    # --- non-admin, with param ---

    def test_non_admin_can_request_own_institution(self):
        user = _make_user(['analyst'], [10, 20])
        result = _call(self.app, user, '10')
        self.assertIn(10, result)

    def test_non_admin_cannot_request_foreign_institution(self):
        user = _make_user(['analyst'], [10, 20])
        result = _call(self.app, user, '99')
        self.assertNotIn(99, result)

    def test_non_admin_gets_intersection(self):
        """Returns the intersection: requested ∩ own."""
        user = _make_user(['analyst'], [10, 20, 30])
        result = _call(self.app, user, '10,30,99')
        self.assertEqual(sorted(result), [10, 30])

    def test_non_admin_all_forbidden_falls_back_to_own(self):
        """If no requested institution is allowed — returns all own."""
        user = _make_user(['analyst'], [10, 20])
        result = _call(self.app, user, '99,100')
        self.assertEqual(sorted(result), [10, 20])

    def test_ct_verifier_same_filtering_as_analyst(self):
        """Filtering is the same for any non-admin role."""
        user = _make_user(['ct_verifier'], [5, 15])
        result = _call(self.app, user, '5,99')
        self.assertEqual(result, [5])

    def test_manager_same_filtering_as_analyst(self):
        user = _make_user(['manager'], [5, 15])
        result = _call(self.app, user, '')
        self.assertEqual(sorted(result), [5, 15])


if __name__ == '__main__':
    unittest.main(verbosity=2)
