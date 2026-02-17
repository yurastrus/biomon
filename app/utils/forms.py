# /app/utils/forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email
from flask_babel import lazy_gettext as _l
from flask_wtf.recaptcha import RecaptchaField

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