"""Docs module (app/docs, submodule shared-docs): access, editing, history, uploads."""
import io
import re
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def docs(app, db_session):
    """Default topics + helpers for creating pages and granting rights."""
    from app.docs.cli import seed_topics
    from app.docs.models import DocEditor, DocPage, DocPageTranslation, DocTopic

    with app.app_context():
        seed_topics()

    class H:
        def topic(self, code):
            return DocTopic.query.filter_by(code=code).one()

        def grant(self, user, *codes):
            for c in codes:
                db_session.add(DocEditor(user_id=user.id, topic_id=self.topic(c).id))
            db_session.commit()

        def page(self, slug='p1', topic='ct', title='Сторінка', body='Текст', **kw):
            p = DocPage(slug=slug, topic_id=self.topic(topic).id, **kw)
            db_session.add(p)
            db_session.flush()
            db_session.add(DocPageTranslation(page_id=p.id, lang='uk', title=title, body=body))
            db_session.commit()
            return p

    return H()


def _login(app, user):
    """Client acting as ``user``. Call it again for every switch of person.

    The db_session fixture keeps one app context open for the whole test, so
    requests share ``g`` and Flask-Login would keep serving the first person it
    loaded (``g._login_user``); drop that cache here.
    """
    from flask import g
    g.pop('_login_user', None)
    c = app.test_client()
    with c.session_transaction() as s:
        s['_user_id'] = str(user.id)
        s['_fresh'] = True
    return c


def _stamp(client, slug, lang='uk'):
    html = client.get(f'/{lang}/docs/{slug}/edit').get_data(as_text=True)
    return re.search(r'name="base_stamp" value="([^"]*)"', html).group(1)


# ── reading ──────────────────────────────────────────────────────────────────

def test_index_lists_topics_for_anonymous(client, docs):
    docs.page('setup', title='Встановлення фотопастки')
    r = client.get('/uk/docs/')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'Фотопастки' in body and 'Встановлення фотопастки' in body
    assert 'Нова сторінка' not in body


def test_english_falls_back_to_ukrainian(client, docs):
    docs.page('setup', title='Встановлення')
    body = client.get('/en/docs/setup').get_data(as_text=True)
    assert 'Встановлення' in body
    assert 'not translated yet' in body


def test_members_only_page_redirects_anonymous_to_login(client, docs):
    docs.page('internal', members_only=True)
    r = client.get('/uk/docs/internal')
    assert r.status_code in (302, 401)


def test_draft_hidden_from_readers(app, client, docs, make_user):
    docs.page('draft', published=False)
    assert client.get('/uk/docs/draft').status_code == 404
    reader = make_user('reader', roles=('viewer',))
    assert _login(app, reader).get('/uk/docs/draft').status_code == 404
    assert 'draft' not in client.get('/uk/docs/').get_data(as_text=True)


# ── topic rights ─────────────────────────────────────────────────────────────

def test_topic_editor_creates_page_only_in_own_topic(app, docs, make_user):
    from app.docs.models import DocPage, DocRevision
    ed = make_user('ct_editor', roles=('viewer',))
    docs.grant(ed, 'ct')
    c = _login(app, ed)

    r = c.post('/uk/docs/new', data={'title': 'Налаштування камери', 'body': 'Текст',
                                     'topic_id': docs.topic('ct').id, 'published': '1'})
    assert r.status_code == 302
    page = DocPage.query.filter_by(slug='nalashtuvannia-kamery').one()
    rev = DocRevision.query.filter_by(page_id=page.id).one()
    assert rev.action == 'create' and rev.user_id == ed.id

    r = c.post('/uk/docs/new', data={'title': 'ПАМ сторінка', 'body': 'x',
                                     'topic_id': docs.topic('pam').id})
    assert r.status_code == 200  # form re-rendered with an error
    assert DocPage.query.count() == 1


def test_reader_cannot_open_editor(app, docs, make_user):
    docs.page('p1')
    u = make_user('nobody', roles=('viewer',))
    c = _login(app, u)
    assert c.get('/uk/docs/p1/edit').status_code == 403
    assert c.get('/uk/docs/new').status_code == 403


def test_edit_records_author_and_shows_it(app, docs, make_user):
    from app.docs.models import DocRevision
    ed = make_user('ed', roles=('viewer',))
    ed.first_name, ed.last_name = 'Олена', 'Коваль'
    docs.grant(ed, 'ct')
    p = docs.page('p1')
    c = _login(app, ed)
    r = c.post('/uk/docs/p1/edit', data={'title': 'Нове', 'body': 'Новий текст',
                                         'note': 'виправив', 'topic_id': p.topic_id,
                                         'published': '1', 'base_stamp': _stamp(c, 'p1')})
    assert r.status_code == 302
    rev = DocRevision.query.filter_by(page_id=p.id, action='edit').one()
    assert (rev.user_id, rev.note, rev.body) == (ed.id, 'виправив', 'Новий текст')
    assert 'Олена Коваль' in c.get('/uk/docs/p1').get_data(as_text=True)


def test_concurrent_edit_is_refused(app, docs, make_user):
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    p = docs.page('p1')
    c = _login(app, ed)
    stale = _stamp(c, 'p1')
    data = {'title': 'A', 'body': 'A', 'topic_id': p.topic_id, 'published': '1'}
    assert c.post('/uk/docs/p1/edit', data={**data, 'base_stamp': stale}).status_code == 302
    r = c.post('/uk/docs/p1/edit', data={**data, 'body': 'B', 'base_stamp': stale})
    assert r.status_code == 200 and 'інший користувач' in r.get_data(as_text=True)


# ── locks ────────────────────────────────────────────────────────────────────

def test_locked_page_is_admin_only(app, docs, make_user):
    from app.docs.models import DocRevision
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'general')
    p = docs.page('licence', topic='general')
    admin = make_user('boss', roles=('admin',))
    assert _login(app, ed).get('/uk/docs/licence/edit').status_code == 200
    assert _login(app, admin).post('/uk/docs/licence/lock').status_code == 302
    assert DocRevision.query.filter_by(page_id=p.id, action='lock').count() == 1

    assert _login(app, ed).get('/uk/docs/licence/edit').status_code == 403
    assert _login(app, admin).get('/uk/docs/licence/edit').status_code == 200
    # Only admins may lock.
    assert _login(app, ed).post('/uk/docs/licence/lock').status_code == 403


# ── admin matrix ─────────────────────────────────────────────────────────────

def test_admin_editor_matrix_saves_rights(app, docs, make_user):
    from app.docs.models import DocEditor
    u = make_user('person', roles=('viewer',))
    a = _login(app, make_user('boss', roles=('admin',)))
    assert a.get('/uk/docs/admin/editors').status_code == 200
    ct, pam = docs.topic('ct').id, docs.topic('pam').id
    a.post('/uk/docs/admin/editors', data={f'u{u.id}_t{ct}': '1', f'u{u.id}_t{pam}': '1'})
    assert {r.topic_id for r in DocEditor.query.filter_by(user_id=u.id)} == {ct, pam}
    a.post('/uk/docs/admin/editors', data={f'u{u.id}_t{pam}': '1'})
    assert {r.topic_id for r in DocEditor.query.filter_by(user_id=u.id)} == {pam}


def test_admin_screens_forbidden_for_editors(app, docs, make_user):
    ed = make_user('ed', roles=('manager',))
    docs.grant(ed, 'ct')
    assert _login(app, ed).get('/uk/docs/admin/editors').status_code == 403


# ── history ──────────────────────────────────────────────────────────────────

def test_restore_old_version(app, docs, make_user):
    from app.docs.models import DocRevision
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    p = docs.page('p1')
    c = _login(app, ed)
    base = {'topic_id': p.topic_id, 'published': '1'}
    c.post('/uk/docs/p1/edit', data={**base, 'title': 'v1', 'body': 'one', 'base_stamp': _stamp(c, 'p1')})
    c.post('/uk/docs/p1/edit', data={**base, 'title': 'v2', 'body': 'two', 'base_stamp': _stamp(c, 'p1')})
    v1 = DocRevision.query.filter_by(page_id=p.id, title='v1').one()
    assert c.get(f'/uk/docs/p1/history/{v1.id}').status_code == 200
    assert c.post(f'/uk/docs/p1/history/{v1.id}/restore').status_code == 302
    assert p.translation('uk').body == 'one'
    assert DocRevision.query.filter_by(page_id=p.id, action='restore').count() == 1


# ── rendering safety ─────────────────────────────────────────────────────────

def test_renderer_blocks_html_and_script_links(app):
    from app.docs.rendering import render
    html = render('<script>alert(1)</script>\n\n[x](javascript:alert(1)) [y](doc:setup) '
                  '![i](/uk/docs/media/abc.png)', lambda s: f'/uk/docs/{s}')
    assert '<script>' not in html
    assert 'javascript:' not in html
    assert 'href="/uk/docs/setup"' in html
    assert 'src="/uk/docs/media/abc.png"' in html


# ── uploads ──────────────────────────────────────────────────────────────────

def _jpeg_with_gps():
    img = Image.new('RGB', (3000, 2000), 'green')
    exif = Image.Exif()
    exif[0x8825] = {2: (49.0, 30.0, 0.0)}  # GPSInfo
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    buf.seek(0)
    return buf


def test_upload_strips_exif_and_downsizes(app, docs, make_user, tmp_path):
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path)
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'pam')
    r = _login(app, ed).post('/uk/docs/media/upload',
                             data={'image': (_jpeg_with_gps(), 'trap.jpg')},
                             content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    name = r.get_json()['url'].rsplit('/', 1)[1]
    saved = Image.open(tmp_path / name)
    assert max(saved.size) == 1600
    assert not saved.getexif()


def test_upload_rejects_non_images_and_readers(app, docs, make_user, tmp_path):
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path)
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'pam')
    r = _login(app, ed).post('/uk/docs/media/upload',
                             data={'image': (io.BytesIO(b'<svg onload=alert(1)>'), 'x.svg')},
                             content_type='multipart/form-data')
    assert r.status_code == 400
    reader = make_user('reader', roles=('viewer',))
    r = _login(app, reader).post('/uk/docs/media/upload',
                                 data={'image': (_jpeg_with_gps(), 'a.jpg')},
                                 content_type='multipart/form-data')
    assert r.status_code == 403


# ── SEO / visibility ─────────────────────────────────────────────────────────

def test_sitemap_lists_only_public_pages_in_existing_languages(client, docs, db_session):
    from app.docs.models import DocPageTranslation
    p = docs.page('public-guide')
    docs.page('internal', members_only=True)
    docs.page('draft', published=False)
    xml = client.get('/sitemap.xml').get_data(as_text=True)
    assert '/uk/docs/public-guide</loc>' in xml
    assert '/en/docs/public-guide</loc>' not in xml  # no English text yet
    assert 'internal' not in xml and '/draft<' not in xml
    assert '/uk/docs/</loc>' in xml
    db_session.add(DocPageTranslation(page_id=p.id, lang='en', title='Guide', body='x'))
    db_session.commit()
    xml = client.get('/sitemap.xml').get_data(as_text=True)
    assert '/en/docs/public-guide</loc>' in xml
    assert 'hreflang="en" href="http://localhost/en/docs/public-guide"' in xml


def test_page_meta_for_crawlers(client, docs):
    docs.page('guide', title='Інструкція')
    uk = client.get('/uk/docs/guide').get_data(as_text=True)
    assert 'content="index, follow"' in uk
    assert 'hreflang="en"' not in uk  # only Ukrainian exists
    en = client.get('/en/docs/guide').get_data(as_text=True)
    assert 'content="noindex, follow"' in en  # fallback duplicate


def test_robots_blocks_editor_screens_only(client):
    txt = client.get('/robots.txt').get_data(as_text=True)
    assert 'Disallow: /*/docs/*/edit' in txt and 'Disallow: /*/docs/admin/' in txt
    assert 'Disallow: /*/docs\n' not in txt


def test_menu_link_admin_only(app, client, docs, make_user):
    assert 'ІНСТРУКЦІЇ' not in client.get('/uk/docs/').get_data(as_text=True)
    ed = make_user('ed', roles=('manager',))
    assert 'ІНСТРУКЦІЇ' not in _login(app, ed).get('/uk/docs/').get_data(as_text=True)
    admin = make_user('boss', roles=('admin',))
    assert 'ІНСТРУКЦІЇ' in _login(app, admin).get('/uk/docs/').get_data(as_text=True)


# ── i18n ─────────────────────────────────────────────────────────────────────

def test_every_module_string_has_english():
    from app.docs.i18n import EN
    root = Path(__file__).resolve().parents[1] / 'app' / 'docs'
    pattern = re.compile(r"""\b_t?\((['"])((?:(?!\1).)+)\1""")
    missing = set()
    for f in list(root.rglob('*.html')) + [root / 'routes.py', root / 'admin_routes.py']:
        text = f.read_text(encoding='utf-8')
        # join implicitly concatenated Python string literals across lines
        text = re.sub(r"""(['"])\s*\n\s*(['"])""", '', text)
        for _, s in pattern.findall(text):
            if s not in EN:
                missing.add(s)
    assert not missing, sorted(missing)
