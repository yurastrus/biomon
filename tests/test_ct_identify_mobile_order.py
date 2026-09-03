# SPDX-License-Identifier: AGPL-3.0-only
"""Reading order of the /identify blocks once the two columns stack.

On a phone the layout collapses to one column, and the sequence then matters:
the photo is what you are looking at, so it must come before the lists you fill
in from it. The rule used to be `.right-column { order: -1 }`, which hoisted the
whole right column — buttons, count and every species name — above the image,
so identifying a series on a phone meant scrolling past the answer options to
reach the question.

These tests read the stylesheet rather than render it. A browser would be the
stronger check, but the question here is narrow and static: which `order` value
does each block get inside the media query, and is the resulting sequence the
intended one. That is answerable from the CSS with no ambiguity.
"""
import pathlib
import re

import pytest

#: Intended top-to-bottom order below 992px. Filters sit above the layout and
#: are not part of it.
EXPECTED_ORDER = [
    'photo-viewer',              # фото
    'action-buttons-container',  # надіслати / пропустити — одразу під фото
    'quantity-panel',            # кількість особин + у вибране
    'species-selection-panel',   # списки видів
    'identification-form-main',  # теги поведінки + коментар (одна карточка)
]


@pytest.fixture(scope='module')
def media_block(request):
    """The <=992px media query, with comments stripped.

    Comments matter: the fix quotes the old `.right-column { order: -1 }` rule
    verbatim to explain itself, and a regex reading declarations would happily
    parse that quotation as live CSS.
    """
    css_path = pathlib.Path(__file__).resolve().parent.parent / \
        'app' / 'camera_traps' / 'static' / 'css' / 'camera_traps.css'
    css = css_path.read_text(encoding='utf-8')
    start = css.index('@media (max-width: 992px) {')
    depth, i = 0, start
    while True:
        if css[i] == '{':
            depth += 1
        elif css[i] == '}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    return re.sub(r'/\*.*?\*/', '', css[start:i], flags=re.S)


def _orders(block):
    return {sel: int(val) for sel, val in
            re.findall(r'\.([a-z-]+)\s*\{[^}]*?order:\s*(-?\d+)', block)}


def test_photo_comes_before_the_species_lists(media_block):
    """The regression this file exists for."""
    orders = _orders(media_block)
    assert orders['photo-viewer'] < orders['species-selection-panel'], \
        'фото знову опинилось нижче за списки видів'


def test_full_order_matches_the_intended_sequence(media_block):
    orders = _orders(media_block)
    present = [sel for _, sel in
               sorted((v, k) for k, v in orders.items() if k in EXPECTED_ORDER)]
    assert present == EXPECTED_ORDER


def test_every_block_has_an_explicit_order(media_block):
    """A block without one falls back to 0 and jumps to the front, which is how
    a later edit quietly breaks the sequence."""
    orders = _orders(media_block)
    missing = [sel for sel in EXPECTED_ORDER if sel not in orders]
    assert not missing, f'без явного order: {missing}'


def test_whole_right_column_is_no_longer_hoisted(media_block):
    """`.right-column { order: -1 }` was the bug: it moved three blocks at once."""
    assert _orders(media_block).get('right-column') != -1


def test_wrappers_are_dissolved_so_order_can_apply(media_block):
    """`order` sorts siblings only, and these blocks live in two columns at two
    levels of nesting; `display: contents` is what makes them siblings."""
    m = re.search(r'((?:\s*\.[a-z-]+,?\s*)+)\{\s*display:\s*contents', media_block)
    assert m, 'display: contents зникло — order більше ні на що не впливає'
    dissolved = set(re.findall(r'\.([a-z-]+)', m.group(1)))
    assert dissolved == {'viewer-and-form', 'right-column'}


def test_form_card_is_not_dissolved(media_block):
    """`.identification-form-main` is a styled card (background, border,
    padding). `display: contents` would drop that box and its styling; tags and
    comment are adjacent in the wanted order anyway, so it moves as one unit."""
    m = re.search(r'((?:\s*\.[a-z-]+,?\s*)+)\{\s*display:\s*contents', media_block)
    assert 'identification-form-main' not in m.group(1)


def test_layout_is_a_flex_column_in_the_media_query(media_block):
    """`order` needs a flex or grid parent; the desktop rule is a two-column
    grid, so the media query has to switch it."""
    assert re.search(r'\.identification-layout\s*\{[^}]*display:\s*flex', media_block)
    assert re.search(r'\.identification-layout\s*\{[^}]*flex-direction:\s*column', media_block)


def test_desktop_layout_is_left_alone():
    """Outside the media query the page must stay a two-column grid."""
    css_path = pathlib.Path(__file__).resolve().parent.parent / \
        'app' / 'camera_traps' / 'static' / 'css' / 'camera_traps.css'
    css = css_path.read_text(encoding='utf-8')
    base = css[:css.index('@media (max-width: 992px) {')]
    m = re.search(r'\.identification-layout\s*\{([^}]*)\}', base)
    assert m and 'display: grid' in m.group(1)
    assert 'grid-template-columns' in m.group(1)


def test_blocks_exist_in_the_template_under_these_class_names(app):
    """The CSS is only as good as the class names it targets; a template rename
    would leave these rules silently inert."""
    html = pathlib.Path(app.root_path, 'camera_traps', 'templates',
                        'identification.html').read_text(encoding='utf-8')
    for sel in EXPECTED_ORDER + ['viewer-and-form', 'right-column']:
        assert f'"{sel}"' in html or f'{sel} ' in html or f' {sel}"' in html, \
            f'класу .{sel} немає в шаблоні'


# ── Filter bar on a phone ────────────────────────────────────────────────────
# The three labelled selects wrapped into a ragged block, each label landing in
# a different place relative to its field. The labels go away below 992px and
# the placeholder option carries the meaning instead.

def test_external_filter_labels_are_hidden_on_mobile(media_block):
    assert re.search(
        r'\.scope-filter-bar\s+\.scope-filter-label\s*\{[^}]*display:\s*none',
        media_block), 'підписи фільтрів знову показуються на телефоні'


def test_filter_selects_take_a_full_row_on_mobile(media_block):
    """Uniform width is the point: mixed widths are what made the bar ragged."""
    m = re.search(r'\.scope-filter-bar\s+\.scope-filter-select\s*\{([^}]*)\}',
                  media_block)
    assert m, 'нема мобільного правила для .scope-filter-select'
    assert 'flex: 1 1 100%' in m.group(1)
    assert 'max-width: none' in m.group(1)


def test_action_buttons_are_a_row_not_a_stack_on_mobile(media_block):
    """Under the photo they are on the way to the species list, so three
    stacked full-width bars would push the lists off the screen."""
    m = re.search(r'\.main-buttons\s*\{([^}]*)\}', media_block)
    assert m and 'flex-direction: row' in m.group(1)


def _identify_html(app):
    return pathlib.Path(app.root_path, 'camera_traps', 'templates',
                        'identification.html').read_text(encoding='utf-8')


def test_both_placeholder_texts_are_rendered_on_the_selects(app):
    """The swap is stateless — it reads both texts off the element — because
    refreshAiSpeciesList() rebuilds the AI options from scratch."""
    html = _identify_html(app)
    for attr in ('data-mobile-placeholder=', 'data-desktop-placeholder='):
        assert html.count(attr) == 2, f'{attr} не на обох селектах'
    assert 'Всі доступні установи' in html
    assert 'Будь-який AI вид' in html


def test_placeholder_swap_reacts_to_the_same_breakpoint_as_the_css(app):
    html = _identify_html(app)
    assert "matchMedia('(max-width: 992px)')" in html
    assert 'applyFilterPlaceholders' in html


def test_swap_is_reapplied_after_the_ai_list_is_rebuilt(app):
    """A scope change recreates the AI options with the desktop wording; without
    this call the mobile placeholder silently reverts."""
    html = _identify_html(app)
    rebuild = html[html.index('function refreshAiSpeciesList'):]
    rebuild = rebuild[:rebuild.index("$('#scope-select').on('change'")]
    assert 'applyFilterPlaceholders();' in rebuild


def test_sort_select_has_no_mobile_placeholder(app):
    """Its options name the field; a placeholder there would be noise."""
    html = _identify_html(app)
    sort_tag = html[html.index('<select id="sort-select"'):]
    sort_tag = sort_tag[:sort_tag.index('>')]
    assert 'data-mobile-placeholder' not in sort_tag
