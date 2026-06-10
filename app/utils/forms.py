# /app/utils/forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Regexp, EqualTo
from flask_babel import lazy_gettext as _l
from flask_wtf.recaptcha import RecaptchaField

from config import Config

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
    new_password = PasswordField(
        _l('Новий пароль'),
        validators=[
            DataRequired(),
            Length(min=Config.PASSWORD_MIN_LENGTH, max=128),
            Regexp(r'(?=.*[A-Za-z])(?=.*\d)',
                   message=_l('Пароль має містити і літери, і цифри.')),
        ]
    )
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