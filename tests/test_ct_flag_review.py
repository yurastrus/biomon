"""
Idea 6 (#19): прапорець «на повторний розгляд» для серій CT.

flag/unflag endpoints, admin-список, доступ. Flag — організаційна позначка
(НЕ змінює status, НЕ виключає з аналітики).

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_flag_review.py -v
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def ct_route_session(ct_session):
    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        yield ct_session


def test_model_has_flag_columns():
    from app.camera_traps.models import Observation
    cols = Observation.__table__.columns.keys()
    assert 'flagged' in cols
    assert 'flag_note' in cols


def test_flag_sets_flagged_and_note(auth_client, db_session, ct_route_session,
                                    make_ct_observation):
    obs = make_ct_observation()
    cl = auth_client(role='ct_verifier')
    resp = cl.post(f'/uk/camera-traps/observation/{obs.id}/flag',
                   data={'note': 'розмита серія'})
    assert resp.status_code in (302, 303)
    ct_route_session.refresh(obs)
    assert obs.flagged is True
    assert obs.flag_note == 'розмита серія'


def test_flag_without_note_sets_null(auth_client, db_session, ct_route_session,
                                     make_ct_observation):
    obs = make_ct_observation()
    cl = auth_client(role='ct_verifier')
    cl.post(f'/uk/camera-traps/observation/{obs.id}/flag', data={})
    ct_route_session.refresh(obs)
    assert obs.flagged is True
    assert obs.flag_note is None


def test_unflag_clears(auth_client, db_session, ct_route_session,
                       make_ct_observation):
    obs = make_ct_observation()
    obs.flagged = True
    obs.flag_note = 'x'
    ct_route_session.commit()
    cl = auth_client(role='ct_verifier')
    cl.post(f'/uk/camera-traps/observation/{obs.id}/unflag', data={})
    ct_route_session.refresh(obs)
    assert obs.flagged is False
    assert obs.flag_note is None


def test_admin_flagged_list_shows_flagged(auth_client, db_session,
                                          ct_route_session, make_ct_observation):
    obs = make_ct_observation()
    obs.flagged = True
    obs.flag_note = 'до перегляду'
    ct_route_session.commit()
    cl = auth_client(role='admin')
    resp = cl.get('/uk/camera-traps/admin/flagged')
    assert resp.status_code == 200
    assert 'до перегляду' in resp.get_data(as_text=True)


def test_flag_requires_ct_verifier(auth_client, db_session, ct_route_session,
                                   make_ct_observation):
    obs = make_ct_observation()
    cl = auth_client(role='viewer')
    resp = cl.post(f'/uk/camera-traps/observation/{obs.id}/flag', data={})
    assert resp.status_code in (302, 403)
    ct_route_session.refresh(obs)
    assert obs.flagged is False


def test_flagged_list_requires_admin(auth_client, db_session, ct_route_session):
    cl = auth_client(role='ct_verifier')
    resp = cl.get('/uk/camera-traps/admin/flagged')
    assert resp.status_code in (302, 403)


def test_lang_code_declared_at_top_level_in_identification_template():
    """
    Регрес-інваріант: переконатись, що `const langCode` оголошено рівно один раз
    і що воно НЕ знаходиться всередині обробника '#fullsize-btn' чи '#flag-btn'.

    Першопричина баґу: langCode був оголошений через `const` всередині
    блоку '#fullsize-btn'.on('click'), тому обробник '#flag-btn' не мав
    до нього доступу → ReferenceError → fetch на /flag не відправлявся.
    """
    import re, pathlib

    template = pathlib.Path(
        __file__
    ).parent.parent / 'app' / 'camera_traps' / 'templates' / 'identification.html'
    text = template.read_text(encoding='utf-8')

    # 1. Рівно одне оголошення const langCode у всьому файлі.
    declarations = re.findall(r'const langCode\s*=', text)
    assert len(declarations) == 1, (
        f"Очікується 1 оголошення `const langCode`, знайдено: {len(declarations)}"
    )

    # 2. Між початком '#fullsize-btn'.on('click' і наступним відповідним `});`
    #    не повинно бути `const langCode`.
    fullsize_match = re.search(r"fullsize-btn['\"]?\)\.on\(", text)
    assert fullsize_match, "#fullsize-btn обробник не знайдено"

    # Шукаємо наступний `});` після початку обробника.
    after_fullsize = text[fullsize_match.start():]
    closing_match = re.search(r'\}\);', after_fullsize)
    assert closing_match, "Кінець обробника #fullsize-btn не знайдено"

    fullsize_body = after_fullsize[:closing_match.end()]
    assert 'const langCode' not in fullsize_body, (
        "const langCode знайдено всередині обробника #fullsize-btn — баґ повернувся!"
    )

    # 3. `const langCode` стоїть ДО позиції обробника #fullsize-btn.
    decl_match = re.search(r'const langCode\s*=', text)
    assert decl_match.start() < fullsize_match.start(), (
        "const langCode оголошено ПІСЛЯ обробника #fullsize-btn, а не на top-level"
    )


def test_flag_then_appears_in_admin_flagged_list(auth_client, db_session,
                                                  ct_route_session,
                                                  make_ct_observation):
    """
    Наскрізний бекенд-регрес (happy path):
      1. Admin (ієрархічно включає ct_verifier) шле POST /flag з нотаткою → 302.
      2. Observation.flagged стає True, flag_note збережено в CT-БД.
      3. Той самий admin клієнт дістає GET /admin/flagged → 200,
         нотатка присутня в HTML.
    Перевіряє зв'язок між endpoint-ом прапорця та чергою перевірки.
    Використовується ОДИН клієнт з роллю admin (яка через ієрархію включає
    ct_verifier), щоб уникнути проблем з ізоляцією між двома test_client.
    """
    obs = make_ct_observation()
    note = 'наскрізний регрес-тест нотатка'

    # Admin ієрархічно включає ct_verifier, тому може виконувати /flag.
    cl = auth_client(role='admin')

    # -- крок 1: flag серії через POST --
    resp = cl.post(
        f'/uk/camera-traps/observation/{obs.id}/flag',
        data={'note': note},
    )
    assert resp.status_code in (302, 303), (
        f"Очікується redirect після /flag, отримано {resp.status_code}"
    )

    # -- крок 2: перевірка стану в CT-БД --
    ct_route_session.refresh(obs)
    assert obs.flagged is True, "Observation.flagged має бути True після POST /flag"
    assert obs.flag_note == note, (
        f"Очікується flag_note={note!r}, отримано {obs.flag_note!r}"
    )

    # -- крок 3: той самий admin клієнт бачить запис у черзі перевірки --
    resp_list = cl.get('/uk/camera-traps/admin/flagged')
    assert resp_list.status_code == 200, (
        f"GET /admin/flagged має повернути 200, отримано {resp_list.status_code}"
    )
    assert note in resp_list.get_data(as_text=True), (
        "flag_note не знайдено на сторінці /admin/flagged"
    )
