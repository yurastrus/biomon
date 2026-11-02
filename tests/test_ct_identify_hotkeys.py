"""Keyboard shortcuts on /identify: fixed digit keys for behaviour tags and the
help block at the bottom of the page.

Tags with behavior_types.hotkey come first, in key order, whatever the usage
ranking says, so a verifier's keys never move; the rest follow by usage.
The keydown handler itself is browser JS and is checked by hand.
"""
import re
from unittest.mock import patch

import pytest

URL = '/uk/camera-traps/identify'


@pytest.fixture
def ct(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'), \
         patch('app.camera_traps.routes.get_species_ranking', return_value={}):
        yield ct_session


@pytest.fixture
def tags(ct):
    from app.camera_traps.models import BehaviorType
    rows = {
        'moving': BehaviorType(name_ua='Переміщення', name_en='Moving', hotkey=1),
        'male': BehaviorType(name_ua='Самець', name_en='Male', hotkey=2),
        'resting': BehaviorType(name_ua='Відпочинок', name_en='Resting', hotkey=10),
        'grooming': BehaviorType(name_ua='Догляд за собою', name_en='Grooming'),
        'biking': BehaviorType(name_ua='Їзда на велосипеді', name_en='Biking'),
    }
    ct.add_all(rows.values())
    ct.commit()
    return rows


def _page(auth_client, ranking):
    with patch('app.camera_traps.routes.get_behavior_ranking', return_value=ranking):
        resp = auth_client(role='ct_verifier').get(URL)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _tag_rows(html):
    """(label, data-hotkey) for every tag, in page order."""
    return re.findall(
        r'<div class="form-check" data-hotkey="(\d?)">.*?<label[^>]*>([^<]+)</label>',
        html, re.S)


def test_keyed_tags_come_first_in_key_order(auth_client, tags):
    # The ranking puts unkeyed Biking on top: it must not take a key's place.
    ranking = {tags['biking'].id: 900, tags['resting'].id: 500,
               tags['male'].id: 10, tags['moving'].id: 1, tags['grooming'].id: 50}
    rows = _tag_rows(_page(auth_client, ranking))

    assert [label for _, label in rows] == [
        'Переміщення', 'Самець', 'Відпочинок',      # keys 1, 2, 0
        'Їзда на велосипеді', 'Догляд за собою',    # the rest by usage
    ]
    assert [key for key, _ in rows] == ['1', '2', '0', '', '']


def test_digit_badges_shown_only_for_keyed_tags(auth_client, tags):
    html = _page(auth_client, {})
    badges = re.findall(r'<kbd class="tag-key"( style="display: none;")?>(\d?)</kbd>', html)
    shown = sorted(text for hidden, text in badges if not hidden)
    assert shown == ['0', '1', '2']
    assert sum(1 for hidden, _ in badges if hidden) == 2


def test_help_block_and_tag_search_rendered(auth_client, tags):
    html = _page(auth_client, {})
    assert 'id="hotkeys-help"' in html
    assert 'id="tag-search"' in html
    assert 'id="hotkeys-hint"' in html
    # Every key the handler knows has a row in the help table.
    help_block = html[html.index('id="hotkeys-help"'):]
    for key in ('←', 'Home', '[', 'Shift+→', 'Shift+←', 'Ctrl+Enter',
                '1…9', 'T', '/', 'F', 'I', 'Ctrl+Shift+T', '?'):
        assert f'<kbd>{key}</kbd>' in help_block, key


def test_hotkey_range_is_enforced(ct):
    """1..10 only (10 is the 0 key), one tag per key."""
    from sqlalchemy.exc import IntegrityError
    from app.camera_traps.models import BehaviorType
    ct.add(BehaviorType(name_ua='A', name_en='A', hotkey=11))
    with pytest.raises(IntegrityError):
        ct.commit()
    ct.rollback()
    ct.add_all([BehaviorType(name_ua='B', name_en='B', hotkey=3),
                BehaviorType(name_ua='C', name_en='C', hotkey=3)])
    with pytest.raises(IntegrityError):
        ct.commit()
    ct.rollback()
