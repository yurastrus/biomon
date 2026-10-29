# SPDX-License-Identifier: AGPL-3.0-only
"""Quick species search on /identify.

A text box above «Загальні опції» filters the species list as you type. The
behaviour itself is checked in a browser; these tests pin the static contract
that would break silently: where the box sits, that it is never submitted with
the identification, and that a new series starts with the full list again.
"""
import pathlib
import re

import pytest


@pytest.fixture(scope='module')
def html():
    return (pathlib.Path(__file__).resolve().parent.parent / 'app' / 'camera_traps'
            / 'templates' / 'identification.html').read_text(encoding='utf-8')


def test_search_box_sits_inside_the_panel_above_general_options(html):
    panel = html.index('class="species-selection-panel"')
    search = html.index('id="species-search"')
    general = html.index("_('Загальні опції')")
    assert panel < search < general


def test_search_box_has_no_name_so_it_is_never_submitted(html):
    tag = re.search(r'<input[^>]*id="species-search"[^>]*>', html, re.S).group(0)
    assert 'name=' not in tag


def test_enter_in_search_does_not_submit_the_form(html):
    handler = html[html.index("$('#species-search')"):]
    handler = handler[:handler.index('});') + 3]
    assert "e.key === 'Enter'" in handler
    assert 'e.preventDefault()' in handler


def test_new_series_resets_the_filter_after_form_reset(html):
    reset = html.index('form[0].reset();')
    refilter = html.index("filterSpecies('')", reset)
    # Same block: the next statement or two, not some later unrelated call.
    assert refilter - reset < 300


def test_search_is_sticky_in_the_scrolling_panel():
    css = (pathlib.Path(__file__).resolve().parent.parent / 'app' / 'camera_traps'
           / 'static' / 'css' / 'camera_traps.css').read_text(encoding='utf-8')
    m = re.search(r'\.species-search\s*\{([^}]*)\}', css)
    assert m and 'position: sticky' in m.group(1)
