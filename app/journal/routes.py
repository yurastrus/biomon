from flask import render_template, g, abort
from collections import defaultdict

from . import journal_bp
from app.models import Issue, JournalArticle, Tag, TagTranslation
from app.extensions import db


@journal_bp.route('/')
def index(lang_code):
    """Головна сторінка журналу зі списком випусків, згрупованих по роках."""
    
    issues_query = Issue.query.filter_by(published=True).order_by(Issue.year.desc(), Issue.number.desc()).all()
    
    # Групуємо випуски за роком
    issues_by_year = defaultdict(list)
    for issue in issues_query:
        issues_by_year[issue.year].append(issue)
        
    return render_template('journal/index.html', issues_by_year=issues_by_year)


@journal_bp.route('/issue/<int:issue_id>')
def issue_detail(lang_code, issue_id):
    """Сторінка конкретного випуску з переліком статей."""
    
    issue = Issue.query.filter_by(id=issue_id, published=True).first_or_404()
    
    # Отримуємо статті цього випуску
    articles = issue.articles.order_by(JournalArticle.created_at.asc()).all()
    
    # Збираємо всі унікальні теги, що є в статтях ЦЬОГО випуску
    tags_in_issue = sorted(
        list(set(tag for article in articles for tag in article.tags)),
        key=lambda t: t.get_translation(g.lang_code).name if t.get_translation(g.lang_code) else t.slug
    )

    return render_template('journal/issue_detail.html', issue=issue, articles=articles, tags_in_issue=tags_in_issue)


@journal_bp.route('/tag/<slug>')
def articles_by_tag(lang_code, slug):
    """Сторінка-архів, що показує всі статті з певним тегом."""
    
    tag = Tag.query.filter_by(slug=slug).first_or_404()
    
    # Знаходимо статті, що мають цей тег і належать до опублікованого випуску
    articles_query = JournalArticle.query.join(JournalArticle.issue).filter(
        Issue.published == True,
        JournalArticle.tags.any(slug=slug)
    ).order_by(JournalArticle.created_at.desc()).all()

    tag_translation = tag.get_translation(g.lang_code)
    tag_name = tag_translation.name if tag_translation else tag.slug

    return render_template('journal/tag_archive.html', articles=articles_query, tag_name=tag_name)