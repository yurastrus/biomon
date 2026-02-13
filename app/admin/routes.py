from flask import render_template, redirect, url_for, flash, request, g
from sqlalchemy import func
from . import admin_bp
from .forms import TagForm
from app.models import Tag, TagTranslation
from app.extensions import db
from flask_babel import gettext as _
from .forms import IssueForm, JournalArticleForm
from app.models import Tag, TagTranslation, Issue, JournalArticle, JournalArticleTranslation
from app.extensions import db
from flask_babel import gettext as _


@admin_bp.route('/')
def dashboard():
    """Головна сторінка адмін-панелі зі статистикою."""
    tag_count = db.session.query(func.count(Tag.id)).scalar()
    issue_count = db.session.query(func.count(Issue.id)).scalar()
    article_count = db.session.query(func.count(JournalArticle.id)).scalar()

    return render_template('dashboard.html', 
                           title=_("Оглядова панель"),
                           tag_count=tag_count,
                           issue_count=issue_count,
                           article_count=article_count)

# === МАРШРУТИ ДЛЯ ВИПУСКІВ ===

@admin_bp.route('/issues')
def list_issues():
    """Відображає список всіх випусків."""
    issues = Issue.query.order_by(Issue.year.desc(), Issue.number.desc()).all()
    return render_template('list_issues.html', issues=issues, title=_("Керування випусками"))

@admin_bp.route('/issues/new', methods=['GET', 'POST'])
def create_issue():
    """Створення нового випуску."""
    form = IssueForm()
    if form.validate_on_submit():
        new_issue = Issue(
            year=form.year.data,
            number=form.number.data,
            published=form.published.data
        )
        db.session.add(new_issue)
        db.session.commit()
        flash(_('Випуск успішно створено!'), 'success')
        return redirect(url_for('admin.list_issues', lang_code=g.lang_code))
    return render_template('edit_issue.html', form=form, title=_("Створити новий випуск"))

@admin_bp.route('/issues/edit/<int:issue_id>', methods=['GET', 'POST'])
def edit_issue(issue_id):
    """Редагування існуючого випуску."""
    issue = Issue.query.get_or_404(issue_id)
    form = IssueForm(obj=issue)
    if form.validate_on_submit():
        issue.year = form.year.data
        issue.number = form.number.data
        issue.published = form.published.data
        db.session.commit()
        flash(_('Випуск успішно оновлено!'), 'success')
        return redirect(url_for('admin.list_issues', lang_code=g.lang_code))
    return render_template('edit_issue.html', form=form, title=_("Редагування випуску"))

@admin_bp.route('/issues/delete/<int:issue_id>', methods=['POST'])
def delete_issue(issue_id):
    """Видалення випуску (разом з усіма статтями в ньому)."""
    issue = Issue.query.get_or_404(issue_id)
    db.session.delete(issue)
    db.session.commit()
    flash(_('Випуск "%(name)s" та всі його статті було видалено.', name=issue.display_name), 'success')
    return redirect(url_for('admin.list_issues', lang_code=g.lang_code))

# === МАРШРУТИ ДЛЯ СТАТЕЙ ЖУРНАЛУ ===

@admin_bp.route('/articles')
def list_articles():
    """Відображає список всіх статей."""
    articles = JournalArticle.query.order_by(JournalArticle.created_at.desc()).all()
    return render_template('list_articles.html', articles=articles, title=_("Керування статтями"))

@admin_bp.route('/articles/new', methods=['GET', 'POST'])
def create_article():
    """Створення нової статті."""
    form = JournalArticleForm()
    if form.validate_on_submit():
        new_article = JournalArticle(
            issue_id=form.issue.data.id,
            pdf_file=form.pdf_file.data,
            tags=form.tags.data # WTForms-SQLAlchemy автоматично опрацює це
        )
        db.session.add(new_article)
        db.session.flush() # Отримуємо ID для статті

        # Створюємо переклади
        trans_uk = JournalArticleTranslation(journal_article_id=new_article.id, lang_code='uk', title=form.uk_title.data, authors=form.uk_authors.data)
        trans_en = JournalArticleTranslation(journal_article_id=new_article.id, lang_code='en', title=form.en_title.data, authors=form.en_authors.data)
        db.session.add_all([trans_uk, trans_en])
        
        db.session.commit()
        flash(_('Статтю успішно створено!'), 'success')
        return redirect(url_for('admin.list_articles', lang_code=g.lang_code))

    return render_template('edit_article.html', form=form, title=_("Створити нову статтю"))

@admin_bp.route('/articles/edit/<int:article_id>', methods=['GET', 'POST'])
def edit_article(article_id):
    """Редагування існуючої статті."""
    article = JournalArticle.query.get_or_404(article_id)
    form = JournalArticleForm(obj=article)
    if form.validate_on_submit():
        article.issue_id = form.issue.data.id
        article.pdf_file = form.pdf_file.data
        article.tags = form.tags.data # Просто присвоюємо новий список тегів
        
        # Оновлюємо переклади
        uk_trans = article.get_translation('uk')
        en_trans = article.get_translation('en')
        if uk_trans:
            uk_trans.title = form.uk_title.data
            uk_trans.authors = form.uk_authors.data
        if en_trans:
            en_trans.title = form.en_title.data
            en_trans.authors = form.en_authors.data

        db.session.commit()
        flash(_('Статтю успішно оновлено!'), 'success')
        return redirect(url_for('admin.list_articles', lang_code=g.lang_code))

    # Заповнення форми даними з БД для GET-запиту
    if request.method == 'GET':
        uk_trans = article.get_translation('uk')
        en_trans = article.get_translation('en')
        if uk_trans:
            form.uk_title.data = uk_trans.title
            form.uk_authors.data = uk_trans.authors
        if en_trans:
            form.en_title.data = en_trans.title
            form.en_authors.data = en_trans.authors

    return render_template('edit_article.html', form=form, title=_("Редагування статті"))

@admin_bp.route('/articles/delete/<int:article_id>', methods=['POST'])
def delete_article(article_id):
    """Видалення статті."""
    article = JournalArticle.query.get_or_404(article_id)
    db.session.delete(article)
    db.session.commit()
    flash(_('Статтю успішно видалено.'), 'success')
    return redirect(url_for('admin.list_articles', lang_code=g.lang_code))

# === МАРШРУТИ ДЛЯ ТЕГІВ ===

@admin_bp.route('/tags')
def list_tags():
    """Відображає список всіх тегів."""
    tags = Tag.query.order_by(Tag.slug).all()
    return render_template('list_tags.html', tags=tags, title=_("Керування тегами"))

@admin_bp.route('/tags/new', methods=['GET', 'POST'])
def create_tag():
    """Створення нового тегу."""
    form = TagForm()
    if form.validate_on_submit():
        if Tag.query.filter_by(slug=form.slug.data).first():
            flash(_('Тег з таким URL (slug) вже існує.'), 'danger')
        else:
            new_tag = Tag(slug=form.slug.data)
            db.session.add(new_tag)
            # Потрібно зробити flush, щоб отримати new_tag.id для перекладів
            db.session.flush() 
            
            trans_uk = TagTranslation(tag_id=new_tag.id, lang_code='uk', name=form.uk_name.data)
            trans_en = TagTranslation(tag_id=new_tag.id, lang_code='en', name=form.en_name.data)
            db.session.add_all([trans_uk, trans_en])
            
            db.session.commit()
            flash(_('Тег успішно створено!'), 'success')
            return redirect(url_for('admin.list_tags', lang_code=g.lang_code))
            
    return render_template('edit_tag.html', form=form, title=_("Створити новий тег"))

@admin_bp.route('/tags/edit/<slug>', methods=['GET', 'POST'])
def edit_tag(slug):
    """Редагування існуючого тегу."""
    tag = Tag.query.filter_by(slug=slug).first_or_404()
    form = TagForm(obj=tag)
    
    if form.validate_on_submit():
        # Slug не змінюється, оновлюємо лише переклади
        uk_trans = tag.get_translation('uk')
        en_trans = tag.get_translation('en')
        
        if uk_trans: uk_trans.name = form.uk_name.data
        if en_trans: en_trans.name = form.en_name.data
        
        db.session.commit()
        flash(_('Тег успішно оновлено!'), 'success')
        return redirect(url_for('admin.list_tags', lang_code=g.lang_code))
    
    # При першому завантаженні (GET) заповнюємо форму даними з БД
    if request.method == 'GET':
        uk_trans = tag.get_translation('uk')
        en_trans = tag.get_translation('en')
        form.uk_name.data = uk_trans.name if uk_trans else ''
        form.en_name.data = en_trans.name if en_trans else ''

    form.slug.render_kw = {'readonly': True} # Забороняємо редагувати slug
    return render_template('edit_tag.html', form=form, title=_("Редагування тега: ") + tag.slug)

@admin_bp.route('/tags/delete/<slug>', methods=['POST'])
def delete_tag(slug):
    """Видалення тегу."""
    tag = Tag.query.filter_by(slug=slug).first_or_404()
    db.session.delete(tag)
    db.session.commit()
    flash(_('Тег "%(slug)s" було успішно видалено.', slug=tag.slug), 'success')
    return redirect(url_for('admin.list_tags', lang_code=g.lang_code))