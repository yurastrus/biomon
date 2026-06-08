"""
Юніт-тести resolve_scope() — дефолтний scope фільтра «Установа / Екорегіон» (#49).

Перевіряє:
  - порожній scope без дефолта → 'global:' (жодна установа не вибрана);
  - CT_DEFAULT_SCOPE='ecoregion:Розточчя' застосовується, ЛИШЕ якщо екорегіон
    доступний користувачу; інакше тихий відкат на 'global:';
  - дефолт-установа застосовується;
  - явний scope з URL завжди має пріоритет над дефолтом;
  - стара поведінка явного екорегіону без установ ([-1]) збережена.

resolve_scope — чиста функція (без app-контексту): використовує лише
i.id та i.ecoregion_uk, тож підставляємо легкі SimpleNamespace-обʼєкти.
"""
import types

from app.camera_traps.routes import resolve_scope


def _inst(id_, eco):
    return types.SimpleNamespace(id=id_, ecoregion_uk=eco)


# Доступні установи: дві в Розточчі (1,2), одна в Карпатах (3)
ROZ = [_inst(1, 'Розточчя'), _inst(2, 'Розточчя'), _inst(3, 'Карпати')]


def test_empty_scope_no_default_is_global():
    scope, ids = resolve_scope('', ROZ)
    assert scope == 'global:'
    assert ids is None


def test_empty_scope_empty_default_is_global():
    scope, ids = resolve_scope('', ROZ, default_scope='')
    assert scope == 'global:'
    assert ids is None


def test_default_ecoregion_applies_when_available():
    scope, ids = resolve_scope('', ROZ, default_scope='ecoregion:Розточчя')
    assert scope == 'ecoregion:Розточчя'
    assert sorted(ids) == [1, 2]


def test_default_ecoregion_falls_back_to_global_when_unavailable():
    """Користувач без установ Розточчя: дефолт ігнорується, повертаємось на «усі»
    (а НЕ на порожній [-1], щоб не показати порожню сторінку)."""
    only_carpathians = [_inst(3, 'Карпати')]
    scope, ids = resolve_scope('', only_carpathians,
                               default_scope='ecoregion:Розточчя')
    assert scope == 'global:'
    assert ids is None


def test_default_institution_applies():
    scope, ids = resolve_scope('', ROZ, default_scope='institution:2')
    assert scope == 'institution:2'
    assert ids == [2]


def test_explicit_scope_overrides_default():
    scope, ids = resolve_scope('institution:3', ROZ,
                               default_scope='ecoregion:Розточчя')
    assert scope == 'institution:3'
    assert ids == [3]


def test_explicit_ecoregion_without_match_keeps_sentinel():
    """Явний (не дефолтний) екорегіон без установ → [-1] — стара поведінка
    збережена (порожній результат, бо користувач свідомо обрав цей фільтр)."""
    scope, ids = resolve_scope('ecoregion:Степ', ROZ)
    assert scope == 'ecoregion:Степ'
    assert ids == [-1]
