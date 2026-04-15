# app/admin/forms.py
"""
WTForms-класи для адмін-панелі.

Відповідають за: обов'язковість полів, обмеження довжини,
формат email та унікальність (перевірка по БД).

Примітка: поля institutions/roles/can_export є списками чекбоксів
у шаблоні й обробляються через request.form.getlist() безпосередньо
в routes/services — не через окремі WTForms-поля, оскільки вони
залежать від поточного requester'а й мають складну логіку.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Optional, Length, Email, ValidationError
from flask_babel import lazy_gettext as _l


class UserCreateForm(FlaskForm):
    """Форма створення користувача (пароль обов'язковий)."""
    username   = StringField(_l('Логін'),     validators=[DataRequired(), Length(min=3, max=20)])
    password   = PasswordField(_l('Пароль'), validators=[DataRequired(), Length(min=6, max=128)])
    email      = StringField(_l('Email'),     validators=[Optional(), Email(), Length(max=120)])
    phone      = StringField(_l('Телефон'),   validators=[Optional(), Length(max=20)])
    first_name = StringField(_l('Ім\'я'),     validators=[Optional(), Length(max=50)])
    last_name  = StringField(_l('Прізвище'), validators=[Optional(), Length(max=50)])

    def validate_username(self, field):
        from app.models import User
        if User.query.filter_by(username=field.data).first():
            raise ValidationError(_l('Користувач з таким іменем вже існує.'))


class UserEditForm(FlaskForm):
    """
    Форма редагування користувача (пароль необов'язковий).
    user_id передається в конструктор, щоб перевірка унікальності
    username не конфліктувала з поточним користувачем.
    """
    username   = StringField(_l('Логін'),        validators=[DataRequired(), Length(min=3, max=20)])
    password   = PasswordField(_l('Новий пароль'), validators=[Optional(), Length(min=6, max=128)])
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
    """Форма створення/редагування установи."""
    name_uk = StringField(_l('Назва (Українською)'), validators=[DataRequired(), Length(max=255)])
    name_en = StringField(_l('Назва (Англійською)'), validators=[Optional(), Length(max=255)])
    code    = StringField(_l('Унікальний код'),       validators=[DataRequired(), Length(max=50)])


class RoleForm(FlaskForm):
    """Форма створення/редагування ролі."""
    name         = StringField(_l('Системна назва'),  validators=[DataRequired(), Length(max=20)])
    assignable_by = StringField(_l('Призначається'), validators=[Optional(), Length(max=20)])
