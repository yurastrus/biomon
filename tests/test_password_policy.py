"""
SEC Phase 3 (#27): політика паролів admin-форм — мін. 8 + літери і цифри.

Стосується лише НОВИХ паролів:
  - UserCreateForm — пароль обов'язковий;
  - UserEditForm — пароль необов'язковий (порожній = «не міняти»).
Існуючі логіни / хеші не зачіпаються (тут не тестуються — окремий потік).

Валідуємо поле password ізольовано (інші поля форми не заповнюємо).
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
    ok, _ = _validate_password(app, CreateForm, 'Ab1xyz')      # 6 символів
    assert ok is False


def test_no_digit_rejected(app, CreateForm):
    ok, _ = _validate_password(app, CreateForm, 'OnlyLetters')  # ≥8, без цифри
    assert ok is False


def test_no_letter_rejected(app, CreateForm):
    ok, _ = _validate_password(app, CreateForm, '12345678')     # ≥8, без літери
    assert ok is False


def test_strong_password_accepted(app, CreateForm):
    ok, errors = _validate_password(app, CreateForm, 'MyStrongPass123')
    assert ok is True, errors


def test_exactly_min_length_with_letter_and_digit_accepted(app, CreateForm):
    ok, errors = _validate_password(app, CreateForm, 'abcde123')  # рівно 8
    assert ok is True, errors


def test_edit_form_blank_password_allowed(app, EditForm):
    """UserEditForm: порожній пароль = «не міняти» → валідний (Optional)."""
    ok, errors = _validate_password(app, EditForm, '', user_id=1)
    assert ok is True, errors


def test_edit_form_weak_password_rejected(app, EditForm):
    """Якщо адмін ВВОДИТЬ новий пароль при редагуванні — політика діє."""
    ok, _ = _validate_password(app, EditForm, 'short1', user_id=1)   # <8
    assert ok is False
