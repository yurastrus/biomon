"""
Absence of a description is NOT a problem, so manage-locations must not
highlight "no description" locations on the map or in the list any more.

Template/CSS/route contract guard.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TPL = (ROOT / 'app' / 'camera_traps' / 'templates' / 'manage_locations.html').read_text(encoding='utf-8')
CSS = (ROOT / 'app' / 'camera_traps' / 'static' / 'css' / 'camera_traps.css').read_text(encoding='utf-8')
ROUTES = (ROOT / 'app' / 'camera_traps' / 'routes.py').read_text(encoding='utf-8')


def test_no_description_category_fully_removed():
    for needle in ('noDescriptionIcon', 'has_description', 'loc-no-description',
                   'marker-icon-violet', 'Без опису'):
        assert needle not in TPL, f'у шаблоні лишилась згадка «{needle}»'
    assert 'loc-no-description' not in CSS, 'у CSS лишилось правило loc-no-description'
    assert 'has_description' not in ROUTES, 'route ще віддає has_description'


def test_other_categories_still_present():
    # The meaningful categories must remain: no-biotope and invalid.
    assert 'noBiotopeIcon' in TPL, 'категорія «без біотопу» помилково зникла'
    assert 'invalidIcon' in TPL, 'категорія «невалідні» помилково зникла'
    assert "_('Без біотопу')" in TPL and "_('Невалідні')" in TPL, 'легенда втратила потрібні категорії'
