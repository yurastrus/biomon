"""
Unit tests for resolve_scope() — the default scope of the "Institution / Ecoregion" filter (#49).

Checks:
  - empty scope without a default → 'global:' (no institution selected);
  - CT_DEFAULT_SCOPE='ecoregion:Розточчя' is applied ONLY if the ecoregion
    is available to the user; otherwise a silent fallback to 'global:';
  - a default institution is applied;
  - an explicit scope from the URL always takes priority over the default;
  - the old behavior of an explicit ecoregion without institutions ([-1]) is preserved.

resolve_scope is a pure function (no app context): it uses only
i.id and i.ecoregion_uk, so we pass lightweight SimpleNamespace objects.
"""
import types

from app.camera_traps.routes import resolve_scope


def _inst(id_, eco):
    return types.SimpleNamespace(id=id_, ecoregion_uk=eco)


# Available institutions: two in Roztochya (1,2), one in the Carpathians (3)
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
    """A user without Roztochya institutions: the default is ignored, we fall back to "all"
    (and NOT to an empty [-1], so as not to show an empty page)."""
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
    """An explicit (non-default) ecoregion without institutions → [-1] — the old behavior
    is preserved (empty result, since the user deliberately chose this filter)."""
    scope, ids = resolve_scope('ecoregion:Степ', ROZ)
    assert scope == 'ecoregion:Степ'
    assert ids == [-1]
