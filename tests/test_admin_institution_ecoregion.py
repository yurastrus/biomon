"""
Institution ecoregion in the admin panel.

`institutions` carries exactly six columns (id, name_uk, name_en, code,
ecoregion_uk, ecoregion_en) and the form used to edit only the first four, so a
new institution could not be assigned to a natural region at all — while the
camera-traps and PAM "Institution / Ecoregion" filters read that very column.

The vocabulary is the set of values already in use: there is no ecoregions table.
Hence the dropdown-plus-"new value" shape, and hence the rule these tests pin —
picking an existing region also stores its English name, so the uk/en pair can
never drift apart.

Run:
    venv/Scripts/python -m pytest tests/test_admin_institution_ecoregion.py -v
"""
import pytest
from flask import g


@pytest.fixture
def institutions(db_session):
    """Three institutions: two with ecoregions (one lacking an en name), one without."""
    from app.models import Institution

    rows = [
        Institution(name_uk='Поліський ПЗ', name_en='Polissia NR', code='PNR-T',
                    ecoregion_uk='Полісся', ecoregion_en='Polissia'),
        Institution(name_uk='Карпатський НПП', name_en='Carpathian NNP', code='CNNP-T',
                    ecoregion_uk='Карпати', ecoregion_en=None),
        Institution(name_uk='Новий проєкт', name_en=None, code='NEW-T'),
    ]
    for r in rows:
        db_session.add(r)
    db_session.commit()
    return rows


def forget_cached_user():
    g.pop('_login_user', None)


# ── the vocabulary ──────────────────────────────────────────────────────────

def test_get_ecoregions_lists_used_values_alphabetically(db_session, institutions):
    from app.admin.services import InstitutionService

    assert InstitutionService.get_ecoregions() == [
        {'uk': 'Карпати', 'en': None},
        {'uk': 'Полісся', 'en': 'Polissia'},
    ]


def test_get_ecoregions_prefers_the_variant_that_has_an_english_name(db_session, institutions):
    """The same region entered twice, once without an en name, must not lose it."""
    from app.models import Institution
    from app.admin.services import InstitutionService

    db_session.add(Institution(name_uk='Ще один', code='X-T',
                               ecoregion_uk='Карпати', ecoregion_en='Carpathians'))
    db_session.commit()

    assert {'uk': 'Карпати', 'en': 'Carpathians'} in InstitutionService.get_ecoregions()


# ── resolving the form input ────────────────────────────────────────────────

def test_resolving_an_existing_choice_brings_its_english_name(db_session, institutions):
    from app.admin.services import InstitutionService

    assert InstitutionService.resolve_ecoregion('Полісся', '', '') == ('Полісся', 'Polissia')


def test_resolving_an_empty_choice_clears_both_columns(db_session, institutions):
    from app.admin.services import InstitutionService

    assert InstitutionService.resolve_ecoregion('', 'ignored', 'ignored') == (None, None)
    assert InstitutionService.resolve_ecoregion(None, None, None) == (None, None)


def test_resolving_a_new_region_uses_the_typed_values(db_session, institutions):
    from app.admin.services import InstitutionService

    assert InstitutionService.resolve_ecoregion('__new__', ' Степ ', ' Steppe ') == \
        ('Степ', 'Steppe')
    assert InstitutionService.resolve_ecoregion('__new__', 'Степ', '') == ('Степ', None)


def test_a_new_region_without_a_ukrainian_name_is_refused(db_session, institutions):
    from app.admin.services import InstitutionService

    with pytest.raises(ValueError):
        InstitutionService.resolve_ecoregion('__new__', '   ', 'Steppe')


def test_an_unknown_choice_is_refused(db_session, institutions):
    """A crafted POST must not smuggle an arbitrary value into the column."""
    from app.admin.services import InstitutionService

    with pytest.raises(ValueError):
        InstitutionService.resolve_ecoregion('Атлантида', '', '')


# ── the form ────────────────────────────────────────────────────────────────

def test_edit_page_offers_used_regions_and_preselects_the_current_one(auth_client, db_session,
                                                                     institutions):
    cl = auth_client(role='admin', username='root_eco')
    forget_cached_user()
    body = cl.get(f'/uk/admin/institutions/edit/{institutions[0].id}').get_data(as_text=True)

    assert 'name="ecoregion_choice"' in body
    assert '<option value="Полісся" selected>' in body, 'current region must be preselected'
    assert '<option value="Карпати" >' in body, 'other used regions must be offered'
    assert '<option value="__new__">' in body, 'must offer adding a region not in the list yet'


def test_add_page_renders_the_dropdown(auth_client, db_session, institutions):
    cl = auth_client(role='admin', username='root_eco2')
    forget_cached_user()
    body = cl.get('/uk/admin/institutions/add').get_data(as_text=True)
    assert 'ecoregion_choice' in body
    assert 'Полісся' in body


def test_assigning_an_existing_region_saves_both_names(auth_client, db_session, institutions):
    from app.models import Institution

    target = institutions[2]          # 'Новий проєкт', no ecoregion
    cl = auth_client(role='admin', username='root_eco3')
    forget_cached_user()
    resp = cl.post(f'/uk/admin/institutions/edit/{target.id}', data={
        'name_uk': target.name_uk, 'code': target.code,
        'ecoregion_choice': 'Полісся',
    })
    assert resp.status_code == 302

    db_session.expire_all()
    saved = db_session.query(Institution).get(target.id)
    assert (saved.ecoregion_uk, saved.ecoregion_en) == ('Полісся', 'Polissia')


def test_creating_an_institution_with_a_brand_new_region(auth_client, db_session, institutions):
    from app.models import Institution

    cl = auth_client(role='admin', username='root_eco4')
    forget_cached_user()
    resp = cl.post('/uk/admin/institutions/add', data={
        'name_uk': 'Дунайський БЗ', 'name_en': 'Danube BR', 'code': 'DBR-T',
        'ecoregion_choice': '__new__',
        'ecoregion_uk': 'Приморські луки', 'ecoregion_en': 'Coastal meadows',
    })
    assert resp.status_code == 302

    saved = db_session.query(Institution).filter_by(code='DBR-T').one()
    assert (saved.ecoregion_uk, saved.ecoregion_en) == ('Приморські луки', 'Coastal meadows')

    # the new value immediately becomes part of the vocabulary
    from app.admin.services import InstitutionService
    assert {'uk': 'Приморські луки', 'en': 'Coastal meadows'} in InstitutionService.get_ecoregions()


def test_clearing_the_region(auth_client, db_session, institutions):
    from app.models import Institution

    target = institutions[0]
    cl = auth_client(role='admin', username='root_eco5')
    forget_cached_user()
    cl.post(f'/uk/admin/institutions/edit/{target.id}', data={
        'name_uk': target.name_uk, 'code': target.code, 'ecoregion_choice': '',
    })

    db_session.expire_all()
    saved = db_session.query(Institution).get(target.id)
    assert (saved.ecoregion_uk, saved.ecoregion_en) == (None, None)


def test_a_new_region_without_a_name_reports_the_error_and_saves_nothing(auth_client, db_session,
                                                                        institutions):
    from app.models import Institution

    target = institutions[2]
    cl = auth_client(role='admin', username='root_eco6')
    forget_cached_user()
    resp = cl.post(f'/uk/admin/institutions/edit/{target.id}', data={
        'name_uk': 'Перейменований', 'code': target.code,
        'ecoregion_choice': '__new__', 'ecoregion_uk': '',
    })

    assert resp.status_code == 200, 'form is re-rendered, not redirected'
    assert 'Вкажіть назву нового' in resp.get_data(as_text=True)

    db_session.expire_all()
    saved = db_session.query(Institution).get(target.id)
    assert saved.name_uk == 'Новий проєкт', 'nothing may be saved on a rejected submit'
    assert saved.ecoregion_uk is None


def test_editing_other_fields_keeps_the_region(auth_client, db_session, institutions):
    """Renaming an institution must not silently drop its ecoregion."""
    from app.models import Institution

    target = institutions[0]
    cl = auth_client(role='admin', username='root_eco7')
    forget_cached_user()
    cl.post(f'/uk/admin/institutions/edit/{target.id}', data={
        'name_uk': 'Поліський природний заповідник', 'code': target.code,
        'ecoregion_choice': 'Полісся',
    })

    db_session.expire_all()
    saved = db_session.query(Institution).get(target.id)
    assert saved.name_uk == 'Поліський природний заповідник'
    assert saved.ecoregion_uk == 'Полісся'


def test_list_page_shows_the_region_and_flags_the_missing_one(auth_client, db_session,
                                                              institutions):
    cl = auth_client(role='admin', username='root_eco8')
    forget_cached_user()
    body = cl.get('/uk/admin/institutions').get_data(as_text=True)
    assert 'Полісся' in body
    assert 'не вказано' in body, 'an institution without a region must be visible as such'
