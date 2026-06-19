"""
Tests for the role_required decorator (app/camera_traps/decorators.py).

Tests the decorator's behavior in isolation:
- blocks users without the required role (triggers redirect + flash)
- lets through users with the required role (calls the original function)
- respects the hierarchy: higher roles include lower ones

Run:
    venv/Scripts/python -m unittest tests.test_access_decorator -v
"""
import unittest
from unittest.mock import MagicMock, patch, call
from flask import g


def _make_user(role_names, authenticated=True):
    from app.models import User
    user = MagicMock()
    user.roles = [MagicMock(name=n) for n in role_names]
    for role, name in zip(user.roles, role_names):
        role.name = name
    user.is_authenticated = authenticated
    # Wire up the real has_role logic instead of the default MagicMock
    user.has_role.side_effect = lambda *args: User.has_role(user, *args)
    return user


def _call_decorated(required_roles, user, *args, **kwargs):
    """
    Wraps an empty function in role_required and calls it,
    mocking current_user, redirect, flash and url_for.
    Returns (was_called, redirect_called).
    """
    from app.camera_traps.decorators import role_required

    called = []

    @role_required(*required_roles)
    def fake_view(*a, **kw):
        called.append(True)
        return 'OK', 200

    with patch('app.camera_traps.decorators.current_user', user), \
         patch('app.camera_traps.decorators.url_for', return_value='/mock-url'), \
         patch('app.camera_traps.decorators.redirect', return_value='REDIRECT') as mock_redirect, \
         patch('app.camera_traps.decorators.flash'):
        result = fake_view(*args, **kwargs)

    return bool(called), mock_redirect.called


class TestRoleRequiredDecorator(unittest.TestCase):

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.test_request_context('/')
        self.ctx.push()
        g.lang_code = 'uk'

    def tearDown(self):
        self.ctx.pop()

    # --- blocking ---

    def test_viewer_blocked_from_ct_verifier_route(self):
        user = _make_user(['viewer'])
        called, redirected = _call_decorated(['ct_verifier'], user)
        self.assertFalse(called)
        self.assertTrue(redirected)

    def test_viewer_blocked_from_analyst_route(self):
        user = _make_user(['viewer'])
        called, redirected = _call_decorated(['analyst'], user)
        self.assertFalse(called)
        self.assertTrue(redirected)

    def test_ct_verifier_blocked_from_manager_route(self):
        user = _make_user(['ct_verifier'])
        called, redirected = _call_decorated(['manager'], user)
        self.assertFalse(called)
        self.assertTrue(redirected)

    def test_analyst_blocked_from_manager_route(self):
        user = _make_user(['analyst'])
        called, redirected = _call_decorated(['manager'], user)
        self.assertFalse(called)
        self.assertTrue(redirected)

    def test_manager_blocked_from_admin_route(self):
        user = _make_user(['manager'])
        called, redirected = _call_decorated(['admin'], user)
        self.assertFalse(called)
        self.assertTrue(redirected)

    def test_no_roles_blocked_everywhere(self):
        user = _make_user([])
        for req in ('viewer', 'ct_verifier', 'analyst', 'manager', 'admin'):
            with self.subTest(required=req):
                called, _ = _call_decorated([req], user)
                self.assertFalse(called)

    # --- passing ---

    def test_ct_verifier_passes_ct_verifier_route(self):
        user = _make_user(['ct_verifier'])
        called, redirected = _call_decorated(['ct_verifier'], user)
        self.assertTrue(called)
        self.assertFalse(redirected)

    def test_analyst_passes_analyst_route(self):
        user = _make_user(['analyst'])
        called, redirected = _call_decorated(['analyst'], user)
        self.assertTrue(called)
        self.assertFalse(redirected)

    def test_manager_passes_manager_route(self):
        user = _make_user(['manager'])
        called, redirected = _call_decorated(['manager'], user)
        self.assertTrue(called)
        self.assertFalse(redirected)

    def test_admin_passes_any_route(self):
        user = _make_user(['admin'])
        for req in ('viewer', 'ct_verifier', 'analyst', 'manager', 'admin'):
            with self.subTest(required=req):
                called, _ = _call_decorated([req], user)
                self.assertTrue(called)

    # --- hierarchy ---

    def test_analyst_passes_ct_verifier_route(self):
        """Analyst includes ct_verifier."""
        user = _make_user(['analyst'])
        called, _ = _call_decorated(['ct_verifier'], user)
        self.assertTrue(called)

    def test_manager_passes_ct_verifier_route(self):
        """Manager includes ct_verifier."""
        user = _make_user(['manager'])
        called, _ = _call_decorated(['ct_verifier'], user)
        self.assertTrue(called)

    def test_manager_passes_analyst_route(self):
        """Manager includes analyst."""
        user = _make_user(['manager'])
        called, _ = _call_decorated(['analyst'], user)
        self.assertTrue(called)

    def test_ct_verifier_blocked_from_analyst_route(self):
        """ct_verifier does NOT include analyst."""
        user = _make_user(['ct_verifier'])
        called, _ = _call_decorated(['analyst'], user)
        self.assertFalse(called)

    # --- not authenticated ---

    def test_unauthenticated_user_is_redirected(self):
        user = _make_user([], authenticated=False)
        called, redirected = _call_decorated(['ct_verifier'], user)
        self.assertFalse(called)
        self.assertTrue(redirected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
