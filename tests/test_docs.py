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
    assert 'Disallow: /*/docs/*/edit$' in txt and 'Disallow: /*/docs/admin/' in txt
    # Exact matches, so pages whose slug starts like a tool URL stay crawlable.
    assert 'Disallow: /*/docs/new$' in txt and 'Disallow: /*/docs/changes$' in txt
    assert 'Disallow: /*/docs\n' not in txt


def test_menu_link_for_everyone(app, client, docs, make_user):
    assert 'ДОКУМЕНТАЦІЯ' in client.get('/uk/').get_data(as_text=True)
    assert 'DOCS' in client.get('/en/').get_data(as_text=True)
    admin = make_user('boss', roles=('admin',))
    html = _login(app, admin).get('/uk/docs/').get_data(as_text=True)
    assert html.index('SDM') < html.index('ДОКУМЕНТАЦІЯ')  # right after SDM for admins


# ── bundles (agent / script path) ────────────────────────────────────────────

def _bundle(tmp_path, uk=None, en=None, image=True):
    folder = tmp_path / 'bundle'
    (folder / 'images').mkdir(parents=True)
    if image:
        Image.new('RGB', (2400, 1200), 'blue').save(folder / 'images' / 'scheme.png')
    if uk is not None:
        (folder / 'uk.md').write_text(uk, encoding='utf-8')
    if en is not None:
        (folder / 'en.md').write_text(en, encoding='utf-8')
    return folder


UK_MD = """---
title: Встановлення фотопастки
slug: camera-trap-setup
topic: ct
note: імпорт з Word
---

## Кріплення

![Схема](images/scheme.png)
"""
EN_MD = """---
title: Camera trap setup
---

## Mounting

![Diagram](images/scheme.png)
"""


def test_bundle_import_creates_bilingual_page_with_image(app, docs, make_user, tmp_path):
    from app.docs.models import DocImage, DocPage, DocRevision
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path / 'media')
    ed = make_user('agent_owner', roles=('viewer',))
    docs.grant(ed, 'ct')
    folder = _bundle(tmp_path, UK_MD, EN_MD)
    r = app.test_cli_runner().invoke(args=['docs', 'import', str(folder), '--author', 'agent_owner'])
    assert r.exit_code == 0, r.output
    page = DocPage.query.filter_by(slug='camera-trap-setup').one()
    assert {t.lang for t in page.translations} == {'uk', 'en'}
    uk = page.translation('uk')
    assert 'images/scheme.png' not in uk.body and '/docs/media/' in uk.body
    assert DocImage.query.count() == 1  # same file used by both languages: one upload
    assert DocImage.query.one().page_id == page.id
    revs = DocRevision.query.filter_by(page_id=page.id).all()
    assert {(r.lang, r.user_id) for r in revs} == {('uk', ed.id), ('en', ed.id)}
    assert next(r.note for r in revs if r.lang == 'uk') == 'імпорт з Word'


def test_bundle_dry_run_writes_nothing(app, docs, make_user, tmp_path):
    from app.docs.models import DocImage, DocPage
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path / 'media')
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    r = app.test_cli_runner().invoke(args=['docs', 'import', str(_bundle(tmp_path, UK_MD, EN_MD)),
                                           '--author', 'ed', '--dry-run'])
    assert r.exit_code == 0 and 'would create' in r.output
    assert DocPage.query.count() == 0 and DocImage.query.count() == 0
    assert not (tmp_path / 'media').exists() or not any((tmp_path / 'media').iterdir())


def test_bundle_respects_rights_update_flag_and_locks(app, docs, make_user, tmp_path):
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path / 'media')
    pam_only = make_user('pam_ed', roles=('viewer',))
    docs.grant(pam_only, 'pam')
    folder = _bundle(tmp_path, UK_MD, image=True)
    run = app.test_cli_runner().invoke
    r = run(args=['docs', 'import', str(folder), '--author', 'pam_ed'])
    assert r.exit_code != 0 and 'no editor rights' in r.output

    ct_ed = make_user('ct_ed', roles=('viewer',))
    docs.grant(ct_ed, 'ct')
    assert run(args=['docs', 'import', str(folder), '--author', 'ct_ed']).exit_code == 0
    r = run(args=['docs', 'import', str(folder), '--author', 'ct_ed'])
    assert r.exit_code != 0 and '--update' in r.output

    from app.docs.models import DocPage
    DocPage.query.filter_by(slug='camera-trap-setup').one().locked = True
    from app.extensions import db
    db.session.commit()
    r = run(args=['docs', 'import', str(folder), '--author', 'ct_ed', '--update'])
    assert r.exit_code != 0 and 'locked' in r.output


def test_bundle_export_import_round_trip(app, docs, make_user, tmp_path):
    from app.docs.models import DocImage, DocRevision
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path / 'media')
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    run = app.test_cli_runner().invoke
    assert run(args=['docs', 'import', str(_bundle(tmp_path, UK_MD, EN_MD)), '--author', 'ed']).exit_code == 0
    out = tmp_path / 'exported'
    r = run(args=['docs', 'export', 'camera-trap-setup', str(out)])
    assert r.exit_code == 0, r.output
    assert (out / 'uk.md').exists() and (out / 'en.md').exists()
    assert len(list((out / 'images').iterdir())) == 1
    before = DocRevision.query.count()
    r = run(args=['docs', 'import', str(out), '--author', 'ed', '--update'])
    assert r.exit_code == 0, r.output
    assert 'text unchanged: uk, en' in r.output
    assert DocRevision.query.count() == before  # nothing changed, nothing logged
    assert DocImage.query.count() == 1          # the exported image was reused


def test_bundle_rejects_missing_image_and_bad_front_matter(app, docs, make_user, tmp_path):
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    run = app.test_cli_runner().invoke
    r = run(args=['docs', 'import', str(_bundle(tmp_path, UK_MD, image=False)), '--author', 'ed',
                  '--dry-run'])
    assert r.exit_code != 0 and 'image not found' in r.output
    bad = tmp_path / 'bad'
    bad.mkdir()
    (bad / 'uk.md').write_text('---\ntitle: X\ncolour: red\n---\ntext', encoding='utf-8')
    r = run(args=['docs', 'import', str(bad), '--author', 'ed', '--dry-run'])
    assert r.exit_code != 0 and 'unknown key' in r.output


def test_example_bundle_is_valid_and_bilingual(app, docs, make_user, make_role):
    """The worked example agents copy from (app/docs/examples) must import."""
    from app.docs.models import DocPage
    root = Path(__file__).resolve().parents[1] / 'app' / 'docs' / 'examples' / 'editing-guides'
    make_role('manager')
    admin = make_user('boss', roles=('admin',))
    r = app.test_cli_runner().invoke(args=['docs', 'import', str(root), '--author', 'boss'])
    assert r.exit_code == 0, r.output
    page = DocPage.query.filter_by(slug='how-to-edit-guides').one()
    assert {t.lang for t in page.translations} == {'uk', 'en'}
    assert page.published is False
    assert [r.name for r in page.visible_roles] == ['manager']
    # Same structure in both languages: same number of sections and steps.
    uk, en = page.translation('uk').body, page.translation('en').body
    assert uk.count('\n## ') == en.count('\n## ')
    assert len(re.findall(r'^\d+\. ', uk, re.M)) == len(re.findall(r'^\d+\. ', en, re.M))
    assert '—' not in uk + en and '–' not in uk + en


# ── visibility by role / editors only ────────────────────────────────────────

def _roles(page, *names):
    from app.models import Role
    from app.extensions import db
    page.visible_roles = Role.query.filter(Role.name.in_(names)).all()
    db.session.commit()


def test_managers_only_page(app, client, docs, make_user, make_role):
    for n in ('manager', 'ct_verifier', 'admin'):
        make_role(n)
    p = docs.page('managers-guide', topic='general')
    p.members_only = True
    _roles(p, 'manager')
    verifier = make_user('ver', roles=('ct_verifier',))
    manager = make_user('man', roles=('manager',))
    admin = make_user('boss', roles=('admin',))

    assert client.get('/uk/docs/managers-guide').status_code in (302, 401)
    assert _login(app, verifier).get('/uk/docs/managers-guide').status_code == 403
    assert 'managers-guide' not in _login(app, verifier).get('/uk/docs/').get_data(as_text=True)
    assert _login(app, manager).get('/uk/docs/managers-guide').status_code == 200
    assert _login(app, admin).get('/uk/docs/managers-guide').status_code == 200
    assert 'managers-guide' not in client.get('/sitemap.xml').get_data(as_text=True)
    page = _login(app, manager).get('/uk/docs/managers-guide').get_data(as_text=True)
    assert 'noindex' in page and 'Менеджери' in page


def test_role_hierarchy_manager_reads_verifier_pages(app, docs, make_user, make_role):
    make_role('ct_verifier')
    p = docs.page('verifier-guide', topic='ct')
    p.members_only = True
    _roles(p, 'ct_verifier')
    assert _login(app, make_user('man', roles=('manager',))).get('/uk/docs/verifier-guide').status_code == 200
    assert _login(app, make_user('pv', roles=('pam_verifier',))).get('/uk/docs/verifier-guide').status_code == 403


def test_editors_only_page(app, docs, make_user):
    from app.extensions import db
    p = docs.page('editor-notes', topic='ct')
    p.editors_only = True
    db.session.commit()
    ct_ed = make_user('ct_ed', roles=('viewer',))
    pam_ed = make_user('pam_ed', roles=('viewer',))
    docs.grant(ct_ed, 'ct')
    docs.grant(pam_ed, 'pam')
    assert _login(app, ct_ed).get('/uk/docs/editor-notes').status_code == 200
    assert _login(app, pam_ed).get('/uk/docs/editor-notes').status_code == 403
    assert _login(app, make_user('reader', roles=('viewer',))).get('/uk/docs/editor-notes').status_code == 403


def test_editor_without_role_cannot_edit_restricted_page(app, docs, make_user, make_role):
    make_role('manager')
    p = docs.page('managers-guide', topic='general')
    _roles(p, 'manager')
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'general')
    assert _login(app, ed).get('/uk/docs/managers-guide/edit').status_code == 403


def test_form_sets_roles_and_refuses_self_lockout(app, docs, make_user, make_role):
    from app.docs.models import DocPage
    manager_role = make_role('manager')
    ed = make_user('ed', roles=('ct_verifier',))
    docs.grant(ed, 'general')
    p = docs.page('guide', topic='general')
    c = _login(app, ed)
    data = {'title': 'Г', 'body': 'т', 'topic_id': p.topic_id, 'published': '1',
            'visibility': 'roles', 'role_ids': [str(manager_role.id)]}
    r = c.post('/uk/docs/guide/edit', data={**data, 'base_stamp': _stamp(c, 'guide')})
    assert r.status_code == 200 and 'втратите доступ' in r.get_data(as_text=True)
    admin = make_user('boss', roles=('admin',))
    a = _login(app, admin)
    r = a.post('/uk/docs/guide/edit', data={**data, 'base_stamp': _stamp(a, 'guide')})
    assert r.status_code == 302
    page = DocPage.query.filter_by(slug='guide').one()
    assert page.visibility == 'roles' and [x.name for x in page.visible_roles] == ['manager']


def test_bundle_roles_key(app, docs, make_user, make_role, tmp_path):
    from app.docs.models import DocPage
    make_role('manager')
    make_user('boss', roles=('admin',))
    md = UK_MD.replace('topic: ct', 'topic: general\nvisibility: roles\nroles: manager')
    run = app.test_cli_runner().invoke
    r = run(args=['docs', 'import', str(_bundle(tmp_path, md, image=False).parent / 'bundle'),
                  '--author', 'boss', '--dry-run'])
    assert 'image not found' in r.output  # UK_MD references a picture
    (tmp_path / 'bundle' / 'images').mkdir(exist_ok=True)
    Image.new('RGB', (10, 10)).save(tmp_path / 'bundle' / 'images' / 'scheme.png')
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path / 'media')
    r = run(args=['docs', 'import', str(tmp_path / 'bundle'), '--author', 'boss'])
    assert r.exit_code == 0, r.output
    page = DocPage.query.filter_by(slug='camera-trap-setup').one()
    assert page.visibility == 'roles' and [x.name for x in page.visible_roles] == ['manager']
    bad = md.replace('roles: manager', 'roles: nobody')
    (tmp_path / 'bundle' / 'uk.md').write_text(bad, encoding='utf-8')
    r = run(args=['docs', 'import', str(tmp_path / 'bundle'), '--author', 'boss', '--update', '--dry-run'])
    assert r.exit_code != 0 and 'unknown role' in r.output


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


def test_browser_crlf_is_stored_as_lf(app, docs, make_user):
    """<textarea> posts CRLF; stored text uses LF so bundle round trips compare equal."""
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    p = docs.page('p1')
    c = _login(app, ed)
    c.post('/uk/docs/p1/edit', data={'title': 'T', 'body': 'a\r\nb\r\n', 'topic_id': p.topic_id,
                                     'published': '1', 'base_stamp': _stamp(c, 'p1')})
    assert p.translation('uk').body == 'a\nb\n'


# ── change tracking and translation sync ─────────────────────────────────────

def _pair(docs, db_session, slug='pair', uk='Перший рядок.\nДругий рядок.\n',
          en='First line.\nSecond line.\n'):
    """A page with both languages, confirmed in step (as after the migration)."""
    from app.docs.models import DocPageTranslation, DocRevision
    p = docs.page(slug, body=uk)
    db_session.add(DocPageTranslation(page_id=p.id, lang='en', title='Page', body=en))
    r_uk = DocRevision(page_id=p.id, page_slug=slug, lang='uk', action='create', title='С', body=uk)
    r_en = DocRevision(page_id=p.id, page_slug=slug, lang='en', action='create', title='Page', body=en)
    db_session.add_all([r_uk, r_en])
    db_session.flush()
    p.translation('uk').synced_with_rev_id = r_en.id
    p.translation('en').synced_with_rev_id = r_uk.id
    db_session.commit()
    return p


def _edit_form(c, slug, lang):
    html = c.get(f'/{lang}/docs/{slug}/edit?lang={lang}').get_data(as_text=True)
    return html, re.search(r'name="base_stamp" value="([^"]*)"', html).group(1)


def test_ukrainian_change_marks_english_outdated_and_shows_what_changed(app, docs, db_session, make_user):
    from app.docs import sync
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    p = _pair(docs, db_session)
    assert sync.lang_state(p, 'en').status == 'ok'
    c = _login(app, ed)
    _, stamp = _edit_form(c, 'pair', 'uk')
    r = c.post('/uk/docs/pair/edit?lang=uk', data={
        'title': 'С', 'body': 'Перший рядок.\nДругий рядок, змінений.\n', 'topic_id': p.topic_id,
        'published': '1', 'base_stamp': stamp})
    assert r.status_code == 302
    assert sync.lang_state(p, 'en').status == 'outdated'
    assert sync.lang_state(p, 'uk').status == 'ok'
    # Readers of the English page are told; the translator gets the Ukrainian diff.
    assert 'updated later' in c.get('/en/docs/pair').get_data(as_text=True)
    edit_en, stamp = _edit_form(c, 'pair', 'en')
    assert '<ins>, змінений</ins>' in edit_en
    assert '/docs/pair' in c.get('/uk/docs/translations').get_data(as_text=True)
    r = c.post('/en/docs/pair/edit?lang=en', data={
        'title': 'Page', 'body': 'First line.\nSecond line, changed.\n', 'topic_id': p.topic_id,
        'published': '1', 'synced': '1', 'base_stamp': stamp})
    assert r.status_code == 302
    assert sync.lang_state(p, 'en').status == 'ok' and sync.lang_state(p, 'uk').status == 'ok'
    assert 'Усі мовні версії узгоджені' in c.get('/uk/docs/translations').get_data(as_text=True)


def test_minor_edit_keeps_other_language_in_step(app, docs, db_session, make_user):
    from app.docs import sync
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    p = _pair(docs, db_session)
    c = _login(app, ed)
    _, stamp = _edit_form(c, 'pair', 'uk')
    c.post('/uk/docs/pair/edit?lang=uk', data={
        'title': 'С', 'body': 'Перший рядок.\nДругий рядок!\n', 'topic_id': p.topic_id,
        'published': '1', 'minor': '1', 'base_stamp': stamp})
    assert p.translation('uk').body.endswith('рядок!\n')
    assert sync.lang_state(p, 'en').status == 'ok'


def test_bundle_with_one_language_changed_warns_and_flags(app, docs, make_user, tmp_path):
    from app.docs import sync
    from app.docs.models import DocPage
    make_user('boss', roles=('admin',))
    run = app.test_cli_runner().invoke
    folder = _bundle(tmp_path, UK_MD, EN_MD)
    app.config['DOCS_UPLOAD_PATH'] = str(tmp_path / 'media')
    assert run(args=['docs', 'import', str(folder), '--author', 'boss']).exit_code == 0
    p = DocPage.query.filter_by(slug='camera-trap-setup').one()
    assert sync.lang_state(p, 'uk').status == 'ok' and sync.lang_state(p, 'en').status == 'ok'
    (folder / 'uk.md').write_text(UK_MD + '\nНовий абзац.\n', encoding='utf-8')
    r = run(args=['docs', 'import', str(folder), '--author', 'boss', '--update'])
    assert r.exit_code == 0 and 'WARNING' in r.output and 'en version' in r.output
    assert sync.lang_state(p, 'en').status == 'outdated'
    (folder / 'en.md').write_text(EN_MD + '\nNew paragraph.\n', encoding='utf-8')
    r = run(args=['docs', 'import', str(folder), '--author', 'boss', '--update'])
    assert 'matching each other' in r.output
    assert sync.lang_state(p, 'en').status == 'ok' and sync.lang_state(p, 'uk').status == 'ok'


def test_revision_diff_highlights_words_and_history_counts(app, docs, make_user):
    from app.docs.models import DocRevision
    ed = make_user('ed', roles=('viewer',))
    docs.grant(ed, 'ct')
    p = docs.page('p1', body='Висота кріплення 50 см.\n')
    c = _login(app, ed)
    for body in ('Висота кріплення 50 см.\n', 'Висота кріплення 60 см.\nНовий рядок.\n'):
        _, stamp = _edit_form(c, 'p1', 'uk')
        c.post('/uk/docs/p1/edit', data={'title': 'С2' if '60' in body else 'С1', 'body': body,
                                         'topic_id': p.topic_id, 'published': '1', 'base_stamp': stamp})
    last = DocRevision.query.filter_by(page_id=p.id).order_by(DocRevision.id.desc()).first()
    html = c.get(f'/uk/docs/p1/history/{last.id}').get_data(as_text=True)
    assert '<del>50</del>' in html and '<ins>60</ins>' in html and '<ins>Новий рядок.</ins>' in html
    hist = c.get('/uk/docs/p1/history').get_data(as_text=True)
    assert '+3' in hist and '−1' in hist


def test_changes_feed_hides_pages_the_reader_cannot_see(app, docs, db_session, make_user, make_role):
    from app.docs.models import DocRevision
    make_role('manager')
    pub = docs.page('public-page')
    secret = docs.page('managers-page', topic='general')
    _roles(secret, 'manager')
    for p in (pub, secret):
        db_session.add(DocRevision(page_id=p.id, page_slug=p.slug, lang='uk', action='edit',
                                   title='x', body='y'))
    db_session.commit()
    viewer = make_user('v', roles=('viewer',))
    html = _login(app, viewer).get('/uk/docs/changes').get_data(as_text=True)
    assert 'public-page' in html and 'managers-page' not in html
    assert _login(app, viewer).get('/uk/docs/translations').status_code == 403


def test_diff_escapes_and_counts():
    from app.docs.diffing import compare
    rows, st = compare('a <b> c\nkeep\n', 'a <i> c\nkeep\n')
    joined = ''.join(str(r.html) for r in rows)
    assert '<del>b</del>' in joined and '<ins>i</ins>' in joined and '&lt;' in joined
    assert (st.added, st.removed) == (1, 1)
    assert compare('same\n', 'same\n')[0] == []


def test_english_table_has_no_duplicate_keys():
    import ast
    src = (Path(__file__).resolve().parents[1] / 'app' / 'docs' / 'i18n.py').read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            dups = {k for k in keys if keys.count(k) > 1}
            assert not dups, dups
