from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, BooleanField, IntegerField, SelectMultipleField
from wtforms.validators import DataRequired, Regexp, ValidationError
from flask_babel import lazy_gettext as _l
from wtforms_sqlalchemy.fields import QuerySelectField, QuerySelectMultipleField

from app.models import Issue, Tag

# --- Форми для тегів (як у вашому прикладі) ---

class TagForm(FlaskForm):
    """Форма для створення та редагування тегів."""
    slug = StringField(_l('URL (Slug)'), validators=[
        DataRequired(),
        Regexp('^[a-z0-9]+(?:-[a-z0-9]+)*$', message=_l('Slug може містити тільки малі літери, цифри та дефіси.'))
    ])
    uk_name = StringField(_l('Назва (UK)'), validators=[DataRequired()])
    en_name = StringField(_l('Назва (EN)'), validators=[DataRequired()])
    submit = SubmitField(_l('Зберегти'))

# --- Форми для випусків та статей (ми їх використаємо на наступному етапі) ---

def get_all_issues():
    """Допоміжна функція для отримання списку випусків."""
    return Issue.query.order_by(Issue.year.desc(), Issue.number.desc())

def get_all_tags():
    """Допоміжна функція для отримання списку тегів."""
    return Tag.query.order_by(Tag.slug)

class IssueForm(FlaskForm):
    """Форма для створення/редагування випуску."""
    year = IntegerField(_l('Рік'), validators=[DataRequired()])
    number = StringField(_l('Номер'), validators=[DataRequired()])
    published = BooleanField(_l('Опубліковано'), default=False)
    submit = SubmitField(_l('Зберегти'))

class JournalArticleForm(FlaskForm):
    """Форма для створення/редагування статті журналу."""
    issue = QuerySelectField(
        _l('Випуск журналу'),
        query_factory=get_all_issues,
        get_label='display_name',
        allow_blank=False,
        validators=[DataRequired()]
    )
    pdf_file = StringField(_l('Шлях до PDF-файлу'), 
                           validators=[DataRequired()],
                           description=_l("Наприклад: troglodytes/2023/petrenko-fauna.pdf"))
    
    uk_title = StringField(_l('Заголовок (UK)'), validators=[DataRequired()])
    uk_authors = StringField(_l('Автори (UK)'), validators=[DataRequired()], description=_l("Напр: Петренко П.І., Сидоренко С.В."))
    
    en_title = StringField(_l('Title (EN)'), validators=[DataRequired()])
    en_authors = StringField(_l('Authors (EN)'), validators=[DataRequired()], description=_l("E.g.: Petrenko P.I., Sydorenko S.V."))
    
    tags = QuerySelectMultipleField(
        _l('Теги'),
        query_factory=get_all_tags,
        get_label=lambda tag: tag.get_translation('uk').name if tag.get_translation('uk') else tag.slug,
        render_kw={'rows': 8} # <--- ПРАВИЛЬНИЙ СПОСІБ
    )
    
    submit = SubmitField(_l('Зберегти статтю'))