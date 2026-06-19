"""
SEC Phase 3 (#27): password policy for admin forms -- min. 8 + letters and digits.

Applies only to NEW passwords:
  - UserCreateForm -- password is required;
  - UserEditForm -- password is optional (empty = "do not change").
Existing logins / hashes are untouched (not tested here -- a separate flow).

We validate the password field in isolation (other form fields are left empty).
"""
import pytest
from config import Config


def _validate_password(app, FormClass, pw, **form_kwargs):
    with app.test_request_context(method='POST', data={'password': pw}):
        form = FormClass(**form_kwargs)
        ok = form.password.validate(form)
        return ok, form.password.errors


@pytest.fixture
def CreateForm():
    from app.admin.forms import UserCreateForm
    return UserCreateForm


@pytest.fixture
def EditForm():
    from app.admin.forms import UserEditForm
    return UserEditForm


def test_min_length_configurable_is_8():
    assert Config.PASSWORD_MIN_LENGTH == 8


def test_too_short_rejected(app, CreateForm):
    ok, _ = _validate_password(app, CreateForm, 'Ab1xyz')      # 6 characters
    assert ok is False


def test_no_digit_rejected(app, CreateForm):
    ok, _ = _validate_password(app, CreateForm, 'OnlyLetters')  # >=8, no digit
    assert ok is False


def test_no_letter_rejected(app, CreateForm):
    ok, _ = _validate_password(app, CreateForm, '12345678')     # >=8, no letter
    assert ok is False


def test_strong_password_accepted(app, CreateForm):
    ok, errors = _validate_password(app, CreateForm, 'MyStrongPass123')
    assert ok is True, errors


def test_exactly_min_length_with_letter_and_digit_accepted(app, CreateForm):
    ok, errors = _validate_password(app, CreateForm, 'abcde123')  # exactly 8
    assert ok is True, errors


def test_edit_form_blank_password_allowed(app, EditForm):
    """UserEditForm: empty password = "do not change" -> valid (Optional)."""
    ok, errors = _validate_password(app, EditForm, '', user_id=1)
    assert ok is True, errors


def test_edit_form_weak_password_rejected(app, EditForm):
    """If the admin DOES enter a new password while editing -- the policy applies."""
    ok, _ = _validate_password(app, EditForm, 'short1', user_id=1)   # <8
    assert ok is False
