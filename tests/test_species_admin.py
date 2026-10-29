"""Admin species editors (CT and PAM) and the Red Book 2021 import matcher.

The editors talk to their module DB through a plain engine, so here each gets
an in-memory SQLite engine with a `species` table of the real shape.
"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from werkzeug.datastructures import MultiDict

from app.camera_traps import species_admin as ct_sa
from app.pam import species_admin as pam_sa


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def ct_engine():
    from app.camera_traps.models import Species
    engine = create_engine('sqlite:///:memory:')
    Species.__table__.create(bind=engine)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO species (id, scientific_name, common_name_ua, category, is_active, "
            "kingdom, phylum, \"class\", order_rank, family, genus) VALUES "
            "(1, 'Lynx lynx', 'Рись', 'mammals', 1, 'Animalia', 'Chordata', 'Mammalia', "
            "'Carnivora', 'Felidae', 'Lynx'), "
            "(2, 'empty', 'Пусто', 'empty', 1, NULL, NULL, NULL, NULL, NULL, NULL)"))
    with patch('app.camera_traps.species_admin.get_ct_engine', return_value=engine):
        yield engine


@pytest.fixture
def pam_engine():
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as c:
        c.execute(text(
            'CREATE TABLE species (species_id INTEGER PRIMARY KEY, scientific_name VARCHAR(255) '
            'NOT NULL UNIQUE, common_name_en VARCHAR(255), common_name_uk VARCHAR(255), '
            'created_at TIMESTAMP, required_role VARCHAR(50), kingdom VARCHAR(255), '
            'phylum VARCHAR(255), "class" VARCHAR(255), order_rank VARCHAR(255), '
            'family VARCHAR(255), genus VARCHAR(255), establishment_means VARCHAR(100), '
            'redbook2021 VARCHAR(30))'))
        c.execute(text(
            "INSERT INTO species (species_id, scientific_name, common_name_uk, \"class\", "
            "order_rank, family, genus, kingdom, phylum) VALUES "
            "(1, 'Bubo bubo', 'Пугач', 'Aves', 'Strigiformes', 'Strigidae', 'Bubo', "
            "'Animalia', 'Chordata')"))
    with patch('app.pam.species_admin.get_pam_engine', return_value=engine):
        yield engine


def _row(engine, sql):
    with engine.connect() as c:
        return c.execute(text(sql)).mappings().first()


# ── pure helpers ────────────────────────────────────────────────────────────

def test_add_new_option_takes_the_typed_value():
    form = MultiDict({'scientific_name': 'Ursus  arctos', 'category': ct_sa.NEW_OPTION,
                      'category__new': ' mammals ', 'family': ct_sa.NEW_OPTION,
                      'family__new': 'Ursidae', 'is_active': '1'})
    values, errors = ct_sa.parse_form(form)
    assert errors == []
    assert values['scientific_name'] == 'Ursus arctos'
    assert values['category'] == 'mammals'
    assert values['family'] == 'Ursidae'
    assert values['is_active'] is True
    assert values['genus'] is None


def test_required_fields_and_unknown_redbook_are_rejected():
    values, errors = ct_sa.parse_form(MultiDict({'redbook2021': 'very_rare'}))
    assert len(errors) == 3  # redbook, scientific_name, category
    assert values['redbook2021'] is None


def test_unchecked_box_means_inactive():
    values, _ = ct_sa.parse_form(MultiDict({'scientific_name': 'X y', 'category': 'other'}))
    assert values['is_active'] is False


def test_pam_role_must_exist():
    _, errors = pam_sa.parse_form(MultiDict({'scientific_name': 'X y', 'required_role': 'nobody'}),
                                  roles=['admin'])
    assert errors
    values, errors = pam_sa.parse_form(MultiDict({'scientific_name': 'X y', 'required_role': 'admin'}),
                                       roles=['admin'])
    assert errors == [] and values['required_role'] == 'admin'


def test_taxonomy_parents_fill_ranks_above():
    rows = [{'kingdom': 'Animalia', 'phylum': 'Chordata', 'class': 'Mammalia',
             'order_rank': 'Carnivora', 'family': 'Felidae', 'genus': 'Lynx'}]
    parents = ct_sa.taxonomy_parents(rows)
    assert parents['genus']['Lynx'] == {'kingdom': 'Animalia', 'phylum': 'Chordata',
                                        'class': 'Mammalia', 'order_rank': 'Carnivora',
                                        'family': 'Felidae'}
    assert parents['kingdom']['Animalia'] == {}


def test_choice_values_are_distinct_and_sorted():
    rows = [{c: None for c in ct_sa.CHOICE_COLUMNS} for _ in range(3)]
    rows[0]['class'], rows[1]['class'], rows[2]['class'] = 'Mammalia', 'Aves', 'Mammalia'
    assert ct_sa.choice_values(rows)['class'] == ['Aves', 'Mammalia']


# ── access ──────────────────────────────────────────────────────────────────

URLS = ('/uk/camera-traps/admin/species', '/uk/camera-traps/admin/species/1',
        '/uk/pam/admin/species', '/uk/pam/admin/species/1')


@pytest.mark.parametrize('url', URLS)
def test_manager_is_turned_away(auth_client, url):
    assert auth_client(role='manager').get(url).status_code in (302, 403)


@pytest.mark.parametrize('url', URLS)
def test_anonymous_is_turned_away(client, url):
    assert client.get(url).status_code in (302, 401, 403)


# ── CT pages ────────────────────────────────────────────────────────────────

def test_ct_list_and_form_render(auth_client, ct_engine):
    c = auth_client(role='admin')
    page = c.get('/uk/camera-traps/admin/species').get_data(as_text=True)
    assert 'Lynx lynx' in page and 'Рись' in page
    form = c.get('/uk/camera-traps/admin/species/1').get_data(as_text=True)
    assert 'value="Felidae" selected' in form
    assert ct_sa.NEW_OPTION in form


def test_ct_edit_saves_every_column(auth_client, ct_engine):
    c = auth_client(role='admin')
    r = c.post('/uk/camera-traps/admin/species/1', data={
        'scientific_name': 'Lynx lynx', 'common_name_ua': 'Рись звичайна',
        'common_name_en': 'Eurasian lynx', 'category': 'mammals', 'kingdom': 'Animalia',
        'phylum': 'Chordata', 'class': 'Mammalia', 'order_rank': 'Carnivora',
        'family': 'Felidae', 'genus': 'Lynx', 'establishment_means': ct_sa.NEW_OPTION,
        'establishment_means__new': 'native', 'redbook2021': 'vulnerable'})
    assert r.status_code == 302
    row = _row(ct_engine, 'SELECT * FROM species WHERE id = 1')
    assert row['common_name_ua'] == 'Рись звичайна'
    assert row['establishment_means'] == 'native'
    assert row['redbook2021'] == 'vulnerable'
    assert not row['is_active']  # box not sent


def test_ct_new_species_and_duplicate(auth_client, ct_engine):
    c = auth_client(role='admin')
    data = {'scientific_name': 'Ursus arctos', 'category': 'mammals', 'is_active': '1'}
    assert c.post('/uk/camera-traps/admin/species/new', data=data).status_code == 302
    assert _row(ct_engine, "SELECT is_active FROM species WHERE scientific_name='Ursus arctos'")
    again = c.post('/uk/camera-traps/admin/species/new',
                   data=dict(data, scientific_name='ursus ARCTOS'))
    assert again.status_code == 400


def test_ct_ai_rows_keep_their_latin_name(auth_client, ct_engine):
    r = auth_client(role='admin').post('/uk/camera-traps/admin/species/2',
                                       data={'scientific_name': 'Empty', 'category': 'empty'})
    assert r.status_code == 400
    assert _row(ct_engine, 'SELECT scientific_name FROM species WHERE id = 2')['scientific_name'] == 'empty'


def test_ct_hub_shows_card_to_admin(auth_client):
    with patch('app.camera_traps.routes.get_ct_session'), \
         patch('app.camera_traps.routes.close_ct_session'):
        page = auth_client(role='admin').get('/uk/camera-traps/')
    assert '/uk/camera-traps/admin/species' in page.get_data(as_text=True)


# ── PAM pages ───────────────────────────────────────────────────────────────

def test_pam_edit_and_new(auth_client, pam_engine, make_role):
    c = auth_client(role='admin')
    page = c.get('/uk/pam/admin/species').get_data(as_text=True)
    assert 'Bubo bubo' in page
    form = c.get('/uk/pam/admin/species/1').get_data(as_text=True)
    assert 'value="Strigidae" selected' in form and 'value="admin"' in form

    r = c.post('/uk/pam/admin/species/1', data={
        'scientific_name': 'Bubo bubo', 'common_name_uk': 'Пугач', 'class': 'Aves',
        'order_rank': 'Strigiformes', 'family': 'Strigidae', 'genus': 'Bubo',
        'redbook2021': 'rare', 'required_role': 'admin'})
    assert r.status_code == 302
    row = _row(pam_engine, 'SELECT * FROM species WHERE species_id = 1')
    assert row['redbook2021'] == 'rare' and row['required_role'] == 'admin'
    assert row['kingdom'] is None  # cleared: every column is the form's to set

    r = c.post('/uk/pam/admin/species/new', data={
        'scientific_name': 'Strix uralensis', 'genus': pam_sa.NEW_OPTION, 'genus__new': 'Strix'})
    assert r.status_code == 302
    assert _row(pam_engine, "SELECT genus FROM species WHERE scientific_name='Strix uralensis'")['genus'] == 'Strix'


# ── Red Book import matcher ─────────────────────────────────────────────────

def test_redbook_matcher_exact_synonym_and_ukrainian_name():
    from scripts.redbook2021.import_redbook_2021 import plan_module
    redlist = [
        {'scientific_name': 'Lynx lynx', 'name_ua': 'Рись', 'category': 'vulnerable', 'class': 'Mammalia'},
        {'scientific_name': 'Aquila clanga', 'name_ua': 'Підорлик великий', 'category': 'rare', 'class': 'Aves'},
        {'scientific_name': 'Foo bar', 'name_ua': 'Видра', 'category': 'rare', 'class': 'Mammalia'},
    ]
    species = [
        {'id': 1, 'scientific_name': 'Lynx lynx', 'name_ua': 'Рись', 'current': None},
        {'id': 2, 'scientific_name': 'Clanga clanga', 'name_ua': 'Підорлик', 'current': None},
        {'id': 3, 'scientific_name': 'Lutra lutra', 'name_ua': 'Видра', 'current': None},
        {'id': 4, 'scientific_name': 'Vulpes vulpes', 'name_ua': 'Лисиця', 'current': 'rare'},
    ]
    matches, ua_only, clears, missing = plan_module(redlist, species, has_column=True)
    assert {(m[0]['id'], m[2]) for m in matches} == {(1, 'exact'), (2, 'synonym')}
    assert [(sp['id'], row['scientific_name']) for sp, row in ua_only] == [(3, 'Foo bar')]
    assert [sp['id'] for sp in clears] == [4, 3] or [sp['id'] for sp in clears] == [3, 4] or \
        [sp['id'] for sp in clears] == [4]
