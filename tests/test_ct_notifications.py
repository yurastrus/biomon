"""
Smoke tests for `app.camera_traps.notifications`.

Cover only safe branches (no mail server):
  • Importing the module does not fail
  • `send_identification_reminders()` without ct_verifier role → (0, 0)
  • Same when the role exists but there are no users with email
"""
import pytest
from unittest.mock import MagicMock, patch

from app.camera_traps import notifications
from app.camera_traps.notifications import send_identification_reminders


def test_module_imports():
    assert hasattr(notifications, 'send_identification_reminders')


def test_no_ct_verifier_role_returns_zero(app, db_session):
    with app.app_context():
        sent, skipped = send_identification_reminders()
    assert (sent, skipped) == (0, 0)


def test_role_without_email_users_returns_zero(app, db_session, make_role):
    role = make_role('ct_verifier')
    db_session.commit()
    assert role.users.count() == 0
    with app.app_context():
        sent, skipped = send_identification_reminders()
    assert (sent, skipped) == (0, 0)
