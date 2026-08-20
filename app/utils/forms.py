# SPDX-License-Identifier: AGPL-3.0-only
# /app/utils/forms.py

from flask_wtf import FlaskForm
from wtforms import (StringField, PasswordField, SubmitField, TextAreaField,
                     BooleanField)
from wtforms.validators import (DataRequired, Email, Length, Regexp, EqualTo,
                                Optional, ValidationError)
from flask_babel import lazy_gettext as _l
from flask_wtf.recaptcha import RecaptchaField

from config import Config

#: Password rules shared by every form that sets a password.
_PASSWORD_VALIDATORS = [
    DataRequired(),
    Length(min=Config.PASSWORD_MIN_LENGTH, max=128),
    Regexp(r'(?=.*[A-Za-z])(?=.*\d)',
           message=_l('Пароль має містити і літери, і цифри.')),
]

class LoginForm(FlaskForm):
    username = StringField(_l('Ім\'я користувача'), validators=[DataRequired()])
    password = PasswordField(_l('Пароль'), validators=[DataRequired()])
    submit = SubmitField(_l('Увійти в систему'))

class ContactForm(FlaskForm):
    name = StringField(_l('Ваше ім\'я'), validators=[DataRequired()])
    email = StringField(_l('Ваш Email'), validators=[DataRequired(), Email()])
    subject = StringField(_l('Тема'))
    message = TextAreaField(_l('Повідомлення'), validators=[DataRequired()])
    recaptcha = RecaptchaField()
    submit = SubmitField(_l('Надіслати'))


class ChangePasswordForm(FlaskForm):
    """Password change form. Minimum length from Config; requires letters and digits."""
    current_password = PasswordField(_l('Поточний пароль'), validators=[DataRequired()])
    new_password = PasswordField(_l('Новий пароль'), validators=_PASSWORD_VALIDATORS)
    confirm_password = PasswordField(
        _l('Підтвердити новий пароль'),
        validators=[DataRequired(), EqualTo('new_password', message=_l('Паролі не співпадають.'))]
    )
    submit_password = SubmitField(_l('Змінити пароль'))


class ChangeUsernameForm(FlaskForm):
    """Username change form. Uniqueness is validated in the route (requires current_user.id)."""
    new_username = StringField(_l('Новий логін'),
                               validators=[DataRequired(), Length(min=3, max=20)])
    submit_username = SubmitField(_l('Змінити логін'))

class NotificationPrefsForm(FlaskForm):
    """Email notification opt-outs.

    Carries only CSRF and the submit button: the checkboxes themselves are
    generated from app.utils.notifications.NOTIFICATION_PREFS in the template
    and read back with request.form, so a new notification type needs no change
    here. Same pattern as the admin institution/role checkbox lists.
    """
    submit_notifications = SubmitField(_l('Зберегти налаштування сповіщень'))


class RegistrationForm(FlaskForm):
    """Public self-service registration.

    Bot defences, in order of cost to a legitimate person: a hidden honeypot
    field (free), reCAPTCHA (one click, same widget as the contact form), and a
    per-IP rate limit in the route. Uniqueness of username/email is checked in
    the route, which owns the DB session.
    """
    username = StringField(
        _l("Ім'я користувача"),
        validators=[DataRequired(), Length(min=3, max=20),
                    Regexp(r'^[A-Za-z0-9_.\-]+$',
                           message=_l('Лише латинські літери, цифри, «_», «.» та «-».'))])
    email = StringField(_l('Email'), validators=[DataRequired(), Email(), Length(max=120)])
    first_name = StringField(_l("Ім'я"), validators=[DataRequired(), Length(max=50)])
    last_name = StringField(_l('Прізвище'), validators=[DataRequired(), Length(max=50)])
    password = PasswordField(_l('Пароль'), validators=_PASSWORD_VALIDATORS)
    confirm_password = PasswordField(
        _l('Підтвердити пароль'),
        validators=[DataRequired(), EqualTo('password', message=_l('Паролі не співпадають.'))])

    wants_ct = BooleanField(_l('Визначати тварин на фото з фотопасток'))
    wants_pam = BooleanField(_l('Визначати голоси тварин на звукозаписах'))

    consent = BooleanField(
        _l('Погоджуюся на обробку вказаних даних для роботи в системі'),
        validators=[DataRequired(message=_l('Без цієї згоди ми не можемо створити акаунт.'))])

    # Honeypot: invisible to people, irresistible to naive bots. Any value = spam.
    website = StringField('Website', validators=[Optional()])

    recaptcha = RecaptchaField()
    submit = SubmitField(_l('Зареєструватися'))

    def validate_website(self, field):
        if field.data:
            raise ValidationError(_l('Помилка перевірки форми.'))

    def validate_wants_pam(self, field):
        # Attached to the last checkbox so the message renders next to them.
        if not (self.wants_ct.data or field.data):
            raise ValidationError(_l('Виберіть хоча б один вид роботи.'))

    @property
    def selected_modules(self):
        """Requested module codes, in the order shown in the form."""
        from app.models import VerificationRequest
        modules = []
        if self.wants_ct.data:
            modules.append(VerificationRequest.MODULE_CT)
        if self.wants_pam.data:
            modules.append(VerificationRequest.MODULE_PAM)
        return modules


class ResendConfirmationForm(FlaskForm):
    """Ask for a fresh confirmation link (the old one expired or never arrived)."""
    email = StringField(_l('Email'), validators=[DataRequired(), Email(), Length(max=120)])
    recaptcha = RecaptchaField()
    submit = SubmitField(_l('Надіслати посилання ще раз'))
