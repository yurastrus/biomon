"""
Idea 6 (#19): "needs re-review" flag for CT series.

flag/unflag endpoints, admin list, access. Flag is an organizational marker
(does NOT change status, does NOT exclude from analytics).

Run:
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
    Regression invariant: ensure `const langCode` is declared exactly once
    and that it is NOT inside the '#fullsize-btn' or '#flag-btn' handler.

    Root cause of the bug: langCode was declared with `const` inside the
    '#fullsize-btn'.on('click') block, so the '#flag-btn' handler had no
    access to it -> ReferenceError -> fetch to /flag was never sent.
    """
    import re, pathlib

    template = pathlib.Path(
        __file__
    ).parent.parent / 'app' / 'camera_traps' / 'templates' / 'identification.html'
    text = template.read_text(encoding='utf-8')

    # 1. Exactly one const langCode declaration in the whole file.
    declarations = re.findall(r'const langCode\s*=', text)
    assert len(declarations) == 1, (
        f"Expected 1 `const langCode` declaration, found: {len(declarations)}"
    )

    # 2. Between the start of '#fullsize-btn'.on('click' and the matching `});`
    #    there must be no `const langCode`.
    fullsize_match = re.search(r"fullsize-btn['\"]?\)\.on\(", text)
    assert fullsize_match, "#fullsize-btn handler not found"

    # Find the next `});` after the handler start.
    after_fullsize = text[fullsize_match.start():]
    closing_match = re.search(r'\}\);', after_fullsize)
    assert closing_match, "End of #fullsize-btn handler not found"

    fullsize_body = after_fullsize[:closing_match.end()]
    assert 'const langCode' not in fullsize_body, (
        "const langCode found inside the #fullsize-btn handler — the bug is back!"
    )

    # 3. `const langCode` comes BEFORE the #fullsize-btn handler position.
    decl_match = re.search(r'const langCode\s*=', text)
    assert decl_match.start() < fullsize_match.start(), (
        "const langCode declared AFTER the #fullsize-btn handler, not at top level"
    )


def test_flag_then_appears_in_admin_flagged_list(auth_client, db_session,
                                                  ct_route_session,
                                                  make_ct_observation):
    """
    End-to-end backend regression (happy path):
      1. Admin (hierarchically includes ct_verifier) sends POST /flag with a note -> 302.
      2. Observation.flagged becomes True, flag_note saved in the CT DB.
      3. The same admin client does GET /admin/flagged -> 200,
         the note is present in the HTML.
    Verifies the link between the flag endpoint and the review queue.
    Uses ONE client with the admin role (which via hierarchy includes
    ct_verifier) to avoid isolation issues between two test_client instances.
    """
    obs = make_ct_observation()
    note = 'наскрізний регрес-тест нотатка'

    # Admin hierarchically includes ct_verifier, so it can perform /flag.
    cl = auth_client(role='admin')

    # -- step 1: flag the series via POST --
    resp = cl.post(
        f'/uk/camera-traps/observation/{obs.id}/flag',
        data={'note': note},
    )
    assert resp.status_code in (302, 303), (
        f"Очікується redirect після /flag, отримано {resp.status_code}"
    )

    # -- step 2: check the state in the CT DB --
    ct_route_session.refresh(obs)
    assert obs.flagged is True, "Observation.flagged має бути True після POST /flag"
    assert obs.flag_note == note, (
        f"Очікується flag_note={note!r}, отримано {obs.flag_note!r}"
    )

    # -- step 3: the same admin client sees the entry in the review queue --
    resp_list = cl.get('/uk/camera-traps/admin/flagged')
    assert resp_list.status_code == 200, (
        f"GET /admin/flagged має повернути 200, отримано {resp_list.status_code}"
    )
    assert note in resp_list.get_data(as_text=True), (
        "flag_note не знайдено на сторінці /admin/flagged"
    )


def test_flagged_list_has_identify_link(auth_client, db_session,
                                        ct_route_session, make_ct_observation):
    """
    The /admin/flagged page must contain an "Identify" link for each
    flagged series, pointing to /identify?start_obs_id=<id>.
    """
    obs = make_ct_observation()
    obs.flagged = True
    obs.flag_note = 'перевірка кнопки'
    ct_route_session.commit()

    cl = auth_client(role='admin')
    resp = cl.get('/uk/camera-traps/admin/flagged')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The «Визначити» button is present
    assert 'Визначити' in html, "Кнопка «Визначити» не знайдена на сторінці /admin/flagged"

    # The link points to /identify with the correct start_obs_id
    expected_url = f'/uk/camera-traps/identify?start_obs_id={obs.id}'
    assert expected_url in html, (
        f"Посилання {expected_url!r} не знайдено на сторінці /admin/flagged. "
        f"Фрагмент HTML: {html[html.find('Визначити')-200:html.find('Визначити')+100]!r}"
    )


def test_next_observation_api_accepts_start_obs_id(auth_client, db_session,
                                                    ct_route_session,
                                                    make_ct_location,
                                                    make_ct_observation,
                                                    make_ct_photo):
    """
    GET /api/next-observation-for-identification?start_obs_id=<id>
    returns 200 and observation_id == the passed id.
    The location is public (visibility_level=0), so accessible to ct_verifier without an institution.
    """
    loc = make_ct_location(visibility_level=0)
    obs = make_ct_observation(location=loc)
    make_ct_photo(observation=obs)

    with patch('app.camera_traps.routes.get_ct_session', return_value=ct_route_session), \
         patch('app.camera_traps.routes.close_ct_session'):
        cl = auth_client(role='ct_verifier')
        resp = cl.get(
            f'/uk/camera-traps/api/next-observation-for-identification'
            f'?start_obs_id={obs.id}'
        )
    assert resp.status_code == 200, (
        f"Очікується 200, отримано {resp.status_code}: {resp.get_data(as_text=True)}"
    )
    data = resp.get_json()
    assert data['observation_id'] == obs.id, (
        f"Очікується observation_id={obs.id}, отримано {data.get('observation_id')}"
    )
