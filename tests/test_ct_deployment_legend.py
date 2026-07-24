"""
manage_deployments map must carry a colour legend with per-row hover tooltips.

Regression guard on the template contract: the legend explains the four marker
colours (blue/gold/red/grey) and each row exposes a `title` tooltip on hover.
"""
import pathlib

CT_TPL = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'camera_traps' / 'templates'
SRC = (CT_TPL / 'manage_deployments.html').read_text(encoding='utf-8')


def test_legend_control_present():
    # A dedicated Leaflet control renders the legend box.
    assert 'dep-legend' in SRC, 'немає легенди (клас .dep-legend)'
    assert "L.control(" in SRC and "legend.addTo(map)" in SRC, 'легенда не додана як control на карту'


def test_legend_covers_all_four_colours():
    for cls in ('lg-blue', 'lg-gold', 'lg-red', 'lg-grey'):
        assert cls in SRC, f'у легенді немає кольору {cls}'


def test_legend_rows_have_hover_tooltips():
    # Each legend row is a help-cursor row carrying a title tooltip.
    assert 'cursor: help' in SRC, 'рядки легенди не позначені як підказки (cursor: help)'
    # One title per colour row → at least four tooltip attributes on legend rows.
    assert SRC.count("class='lg-row' title=") >= 4, 'не всі рядки легенди мають tooltip (title)'
