"""
CT identify page: 'Other' category improvements.

build_identify_species_groups() must:
  - route negative-id placeholder species by their real `category`
    (birds/mammals), not dump every id<0 row into an undifferentiated bucket;
  - carve domestic animals (dog/cat/cow/sheep/goat/horse) into their own
    'domestic' group, positioned between 'mammals' and 'birds';
  - sort every group (including 'other') by identification frequency.
"""
from types import SimpleNamespace

from app.camera_traps.routes import build_identify_species_groups


def _sp(id, category, common_name_ua='Назва', scientific_name='Genus species'):
    return SimpleNamespace(
        id=id,
        category=category,
        scientific_name=scientific_name,
        common_name_ua=common_name_ua,
        common_name_en=None,
    )


def test_empty_species_is_isolated():
    grouped, empty_choices = build_identify_species_groups(
        [_sp(-1, 'empty', 'Пусто')], 'uk', {}
    )
    assert empty_choices == [(-1, 'Пусто')]
    assert all(not v for v in grouped.values())


def test_negative_id_bird_goes_to_birds_not_other():
    owl = _sp(-25, 'birds', 'Сова')
    grouped, _ = build_identify_species_groups([owl], 'uk', {})
    assert grouped['birds'] == [(-25, 'Сова')]
    assert grouped['other'] == []


def test_negative_id_mammal_goes_to_mammals():
    mustelid = _sp(-20, 'mammals', 'Куницеві')
    grouped, _ = build_identify_species_groups([mustelid], 'uk', {})
    assert grouped['mammals'] == [(-20, 'Куницеві')]
    assert grouped['other'] == []


def test_domestic_species_get_own_group_between_mammals_and_birds():
    dog = _sp(8, 'mammals', 'Собака свійський', 'Canis familiaris')
    cow = _sp(-10, 'mammals', 'Корова')
    grouped, _ = build_identify_species_groups([dog, cow], 'uk', {})

    assert {c[0] for c in grouped['domestic']} == {8, -10}
    assert grouped['mammals'] == []

    group_order = list(grouped.keys())
    assert group_order.index('mammals') < group_order.index('domestic') < group_order.index('birds')


def test_unknown_category_falls_back_to_other():
    insect = _sp(-24, 'other', 'Жуки')
    unknown = _sp(100, 'reptiles', 'Ящірка')
    grouped, _ = build_identify_species_groups([insect, unknown], 'uk', {})
    assert {c[0] for c in grouped['other']} == {-24, 100}


def test_other_group_sorted_by_frequency():
    common = _sp(-24, 'other', 'Жуки')
    rare = _sp(-2, 'other', 'Інший вид')
    ranking = {-24: 50, -2: 1}
    grouped, _ = build_identify_species_groups([rare, common], 'uk', ranking)
    assert grouped['other'] == [(-24, 'Жуки'), (-2, 'Інший вид')]


def test_display_name_uses_ukrainian_common_name_with_scientific_in_brackets():
    fox = _sp(1, 'mammals', 'Лисиця', 'Vulpes vulpes')
    grouped, _ = build_identify_species_groups([fox], 'uk', {})
    assert grouped['mammals'] == [(1, 'Лисиця (Vulpes vulpes)')]


def test_display_name_for_special_id_omits_scientific_name_brackets():
    owl = _sp(-25, 'birds', 'Сова', 'strigiform')
    grouped, _ = build_identify_species_groups([owl], 'uk', {})
    assert grouped['birds'] == [(-25, 'Сова')]
