"""
Test verifying redirect of an unauthenticated user to the login page.

Run:
    venv/Scripts/python -m unittest tests.test_auth_redirect -v
"""
import os
import unittest
from unittest.mock import patch, MagicMock


class TestAuthRedirect(unittest.TestCase):

    def setUp(self):
        """Create a test app with DB stubs."""
        # Override the URI before create_app so we don't connect to PostgreSQL
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

        ct_engine_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock()
        )
        ct_engine_patcher.start()
        self.addCleanup(ct_engine_patcher.stop)

        from app import create_app
        self.app = create_app('testing')

        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('DATABASE_URL', None)

    def test_unauthenticated_identify_redirects_to_login(self):
        """
        GET /uk/camera-traps/identify without a session must redirect
        to /uk/login with a next parameter, not return 500.
        """
        response = self.client.get('/uk/camera-traps/identify')

        self.assertEqual(
            response.status_code, 302,
            f"Очікувався redirect (302), отримано {response.status_code}"
        )

        location = response.headers.get('Location', '')
        self.assertIn('/uk/login', location,
                      f"Redirect має вести на /uk/login, а не на: {location}")
        self.assertIn('next=', location,
                      f"У redirect URL має бути параметр next: {location}")

    def test_unauthenticated_identify_next_points_to_identify(self):
        """The next parameter must contain /uk/camera-traps/identify."""
        response = self.client.get('/uk/camera-traps/identify')
        location = response.headers.get('Location', '')
        self.assertIn('camera-traps/identify', location,
                      f"next має вказувати на identify: {location}")

    def test_login_page_accessible_without_auth(self):
        """Login page is accessible without authentication (not 500)."""
        response = self.client.get('/uk/login')
        self.assertNotEqual(response.status_code, 500,
                            "Сторінка /uk/login не повинна повертати 500")
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
