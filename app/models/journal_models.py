from app.extensions import db
from datetime import datetime

# Проміжна таблиця для зв'язку "багато-до-багатьох" між Статтями та Тегами
journal_article_tags = db.Table('journal_article_tags',
    db.Column('article_id', db.Integer, db.ForeignKey('journal_article.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)

# === МОДЕЛІ ДЛЯ ТЕГІВ (майже без змін з вашого прикладу) ===

class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(128), unique=True, nullable=False, index=True)
    
    translations = db.relationship('TagTranslation', back_populates='tag', cascade='all, delete-orphan')
    
    def get_translation(self, lang_code):
        for t in self.translations:
            if t.lang_code == lang_code:
                return t
        return None

    def __repr__(self):
        return f'<Tag {self.slug}>'

class TagTranslation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tag.id'), nullable=False)
    lang_code = db.Column(db.String(5), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    
    tag = db.relationship('Tag', back_populates='translations')
    __table_args__ = (db.UniqueConstraint('tag_id', 'lang_code', name='_tag_lang_uc'),)

    def __repr__(self):
        return f'<TagTranslation {self.tag.slug} ({self.lang_code})>'

# === НОВІ МОДЕЛІ ДЛЯ ЖУРНАЛУ ===

class Issue(db.Model):
    """Модель для випуску журналу."""
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    number = db.Column(db.String(20), nullable=False) # Напр. "1" або "1-2"
    published = db.Column(db.Boolean, default=False, nullable=False)

    articles = db.relationship('JournalArticle', back_populates='issue', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def display_name(self):
        """Повертає зручне для читання ім'я випуску."""
        return f"Troglodytes {self.year}, №{self.number}"

    def __repr__(self):
        return f'<Issue {self.year} #{self.number}>'

class JournalArticle(db.Model):
    """Модель для окремої статті в журналі."""
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issue.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Шлях до файлу відносно папки 'static'
    pdf_file = db.Column(db.String(255), nullable=False, unique=True)
    
    # Зв'язки
    issue = db.relationship('Issue', back_populates='articles')
    translations = db.relationship('JournalArticleTranslation', back_populates='article', cascade='all, delete-orphan')
    tags = db.relationship('Tag', secondary=journal_article_tags, back_populates='articles')

    def get_translation(self, lang_code):
        for t in self.translations:
            if t.lang_code == lang_code:
                return t
        return None

    def __repr__(self):
        # Спробуємо знайти український заголовок для кращого представлення
        uk_trans = self.get_translation('uk')
        title = uk_trans.title if uk_trans else f"Article ID: {self.id}"
        return f'<JournalArticle: {title}>'

class JournalArticleTranslation(db.Model):
    """Переклади метаданих для статті журналу (назва, автори)."""
    id = db.Column(db.Integer, primary_key=True)
    journal_article_id = db.Column(db.Integer, db.ForeignKey('journal_article.id'), nullable=False)
    lang_code = db.Column(db.String(5), nullable=False)
    
    title = db.Column(db.String(300), nullable=False)
    authors = db.Column(db.String(300), nullable=False) # "Петренко П.П., Іваненко І.І."

    article = db.relationship('JournalArticle', back_populates='translations')
    __table_args__ = (db.UniqueConstraint('journal_article_id', 'lang_code', name='_journal_article_lang_uc'),)

    def __repr__(self):
        return f'<{self.article_id} - {self.lang_code}: {self.title}>'
        
# Пов'язуємо зворотній зв'язок для Tag -> JournalArticle
# Це дозволить нам легко отримати всі статті для певного тега
Tag.articles = db.relationship('JournalArticle', secondary=journal_article_tags, back_populates='tags')