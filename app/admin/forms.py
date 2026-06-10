"""WTForms classes for the admin panel.

Responsible for: field requirements, length limits, email format validation,
and username uniqueness checks against the database.

Note: institutions/roles/can_export fields are checkbox lists rendered in the
template and processed via request.form.getlist() in routes/services, not as
dedicated WTForms fields, because their options depend on the requester and
involve complex access-control logic.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Optional, Length, Email, ValidationError, Regexp
from flask_babel import lazy_gettext as _l

from config import Config

# Password policy: minimum length from Config.PASSWORD_MIN_LENGTH;
# complexity requires letters AND digits (no special-char requirement, UX-friendly).
# Applies to new passwords only (creation / admin reset); existing hashes are untouched.
_PW_COMPLEXITY = Regexp(
    r'(?=.*[A-Za-z])(?=.*\d)',
    message=_l('Пароль має містити і літери, і цифри.')
)


class UserCreateForm(FlaskForm):
    """User creation form (password is required)."""
    username   = StringField(_l('Логін'),     validators=[DataRequired(), Length(min=3, max=20)])
    password   = PasswordField(_l('Пароль'), validators=[DataRequired(), Length(min=Config.PASSWORD_MIN_LENGTH, max=128), _PW_COMPLEXITY])
    email      = StringField(_l('Email'),     validators=[Optional(), Email(), Length(max=120)])
    phone      = StringField(_l('Телефон'),   validators=[Optional(), Length(max=20)])
    first_name = StringField(_l('Ім\'я'),     validators=[Optional(), Length(max=50)])
    last_name  = StringField(_l('Прізвище'), validators=[Optional(), Length(max=50)])

    def validate_username(self, field):
        from app.models import User
        if User.query.filter_by(username=field.data).first():
            raise ValidationError(_l('Користувач з таким іменем вже існує.'))


class UserEditForm(FlaskForm):
    """User edit form (password is optional).

    user_id is passed to the constructor so the username uniqueness check
    does not conflict with the user's own current username.
    """
    username   = StringField(_l('Логін'),        validators=[DataRequired(), Length(min=3, max=20)])
    password   = PasswordField(_l('Новий пароль'), validators=[Optional(), Length(min=Config.PASSWORD_MIN_LENGTH, max=128), _PW_COMPLEXITY])
    email      = StringField(_l('Email'),          validators=[Optional(), Email(), Length(max=120)])
    phone      = StringField(_l('Телефон'),        validators=[Optional(), Length(max=20)])
    first_name = StringField(_l('Ім\'я'),          validators=[Optional(), Length(max=50)])
    last_name  = StringField(_l('Прізвище'),       validators=[Optional(), Length(max=50)])

    def __init__(self, user_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_id = user_id

    def validate_username(self, field):
        from app.models import User
        existing = User.query.filter_by(username=field.data).first()
        if existing and existing.id != self._user_id:
            raise ValidationError(_l('Користувач з таким іменем вже існує.'))


class InstitutionForm(FlaskForm):
    """Institution create/edit form."""
    name_uk = StringField(_l('Назва (Українською)'), validators=[DataRequired(), Length(max=255)])
    name_en = StringField(_l('Назва (Англійською)'), validators=[Optional(), Length(max=255)])
    code    = StringField(_l('Унікальний код'),       validators=[DataRequired(), Length(max=50)])


class RoleForm(FlaskForm):
    """Role create/edit form."""
    name         = StringField(_l('Системна назва'),  validators=[DataRequired(), Length(max=20)])
    assignable_by = StringField(_l('Призначається'), validators=[Optional(), Length(max=20)])
