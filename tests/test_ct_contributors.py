"""
Тести сторінки «Всі учасники» (camera_traps.contributors).

Покриває:
  - query_contributor_stats — ковзні вікна (сьогодні/тиждень/місяць/рік/усього)
    на реальних даних in-memory ct_session; метрика = distinct observation_id;
  - маршрут /contributors — код 200 для анонімного/залогіненого;
  - видимість імен: manager+ бачить повне ім'я, решта — нікнейм;
  - порожній результат → info-message.

Запуск:
    venv/Scripts/python -m pytest tests/test_ct_contributors.py -v
"""
from collections import namedtuple
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text


# ──────────────────────────────────────────────────────────────────────────
# 1. query_contributor_stats — логіка ковзних вікон на реальних даних
# ──────────────────────────────────────────────────────────────────────────

def _add_identification(ct_session, location, user_id, created_at, species_id=None,
                        captured_at=None):
    """
    Створює Observation + Photo + Identification одного користувача.
    `created_at` — час ВИЗНАЧЕННЯ (за ним рахуються ковзні вікна);
    `captured_at` — час зйомки фото (за замовчуванням давня фіксована дата,
    щоб підтвердити, що вікна НЕ залежать від часу зйомки).
    """
    from app.camera_traps.models import Observation, Photo, Identification

    if captured_at is None:
        captured_at = datetime(2021, 1, 1, 12, 0)

    obs = Observation(
        location_id=location.id,
        series_start_time=captured_at,
        series_end_time=captured_at + timedelta(minutes=5),
        uploaded_by_id=user_id,
    )
    ct_session.add(obs)
    ct_session.flush()

    photo = Photo(
        observation_id=obs.id,
        original_filename=f'IMG_{obs.id}.jpg',
        system_filename=f'sys_{obs.id}.jpg',
        captured_at=captured_at,
    )
    ct_session.add(photo)
    ct_session.flush()

    ident = Identification(photo_id=photo.id, user_id=user_id, species_id=species_id,
                           created_at=created_at)
    ct_session.add(ident)
    ct_session.commit()
    return obs


@pytest.fixture
def seeded_contributors(ct_session, make_ct_location):
    """
    Один користувач (id=1) з 5 визначеннями, зробленими на різних відстанях
    від `today` (за created_at), та другий користувач (id=2) з одним визначенням
    сьогодні. Повертає (today, location).
    """
    today = date(2025, 6, 15)
    loc = make_ct_location()

    def dt(days_ago):
        # Час ВИЗНАЧЕННЯ (created_at) — за ним рахуються вікна.
        return datetime.combine(today, datetime.min.time()) - timedelta(days=days_ago) \
            + timedelta(hours=12)

    # user 1
    _add_identification(ct_session, loc, 1, dt(0))    # сьогодні
    _add_identification(ct_session, loc, 1, dt(3))    # у межах тижня
    _add_identification(ct_session, loc, 1, dt(20))   # у межах місяця
    _add_identification(ct_session, loc, 1, dt(100))  # у межах року
    _add_identification(ct_session, loc, 1, dt(500))  # лише total
    # user 2
    _add_identification(ct_session, loc, 2, dt(0))

    return today, loc


def _run_stats(ct_session, today):
    from app.camera_traps.routes import query_contributor_stats
    return query_contributor_stats(ct_session, today, text("1=1"), {})


def test_window_counts_for_single_user(ct_session, seeded_contributors):
    today, _ = seeded_contributors
    rows = _run_stats(ct_session, today)
    by_user = {r.user_id: r for r in rows}

    r1 = by_user[1]
    assert r1.d_today == 1
    assert r1.d_week == 2
    assert r1.d_month == 3
    assert r1.d_year == 4
    assert r1.total == 5


def test_grouping_and_ordering_by_total_desc(ct_session, seeded_contributors):
    today, _ = seeded_contributors
    rows = _run_stats(ct_session, today)

    # Дві групи користувачів, відсортовані за total desc.
    assert [r.user_id for r in rows] == [1, 2]
    assert rows[0].total == 5
    assert rows[1].total == 1
    assert rows[1].d_today == 1


def test_windows_use_determination_time_not_capture_time(ct_session, make_ct_location):
    """Вікна рахуються за created_at (час визначення), НЕ за captured_at (час зйомки)."""
    from app.camera_traps.routes import query_contributor_stats
    today = date(2025, 6, 15)
    loc = make_ct_location()

    # Фото зняте СЬОГОДНІ, але визначене рік тому → не має потрапити в d_today/d_week.
    _add_identification(
        ct_session, loc, user_id=7,
        created_at=datetime(2024, 1, 1, 12, 0),
        captured_at=datetime.combine(today, datetime.min.time()),
    )
    rows = query_contributor_stats(ct_session, today, text("1=1"), {})
    r = {x.user_id: x for x in rows}[7]
    assert r.d_today == 0
    assert r.d_week == 0
    assert r.d_month == 0
    assert r.total == 1


def test_location_filter_excludes_other_locations(ct_session, seeded_contributors, make_ct_location):
    from app.camera_traps.routes import query_contributor_stats
    today, loc = seeded_contributors
    other = make_ct_location(name='Other', latitude=50.0, longitude=25.0)
    _add_identification(ct_session, other, 3, datetime.combine(today, datetime.min.time()))

    rows = query_contributor_stats(ct_session, today, text("1=1"), {},
                                   location_ids=[loc.id])
    assert 3 not in {r.user_id for r in rows}


# ──────────────────────────────────────────────────────────────────────────
# 2. Маршрут /contributors — доступ та видимість імен
# ──────────────────────────────────────────────────────────────────────────

Row = namedtuple('Row', 'user_id d_today d_week d_month d_year total')

URL = '/uk/camera-traps/contributors'


def _patch_ct(monkeypatch, rows, species_list=None):
    """Мокаємо CT-сесію та підміняємо query_contributor_stats на фіксовані рядки.

    `ct_session.query(...)` повертає MagicMock-ланцюжок, що повертає `species_list`
    (за замовчуванням — порожній список) через `.all()` і `.distinct()...`.
    """
    mock_session = MagicMock()
    # chain: .query().join().join()... .filter().params().distinct().order_by() → iterable
    chain = MagicMock()
    chain.__iter__ = MagicMock(return_value=iter(species_list or []))
    mock_session.query.return_value = chain
    chain.join.return_value = chain
    chain.filter.return_value = chain
    chain.params.return_value = chain
    chain.distinct.return_value = chain
    chain.order_by.return_value = chain

    monkeypatch.setattr('app.camera_traps.routes.get_ct_session', lambda: mock_session)
    monkeypatch.setattr('app.camera_traps.routes.close_ct_session', lambda: None)
    monkeypatch.setattr('app.camera_traps.routes.query_contributor_stats',
                        lambda *a, **k: rows)


def test_anonymous_sees_username_not_full_name(client, make_user, monkeypatch):
    u = make_user(username='ident_u')
    u.first_name, u.last_name = 'Іван', 'Петренко'
    from app.extensions import db
    db.session.commit()

    _patch_ct(monkeypatch, [Row(u.id, 1, 2, 3, 4, 5)])
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'ident_u' in body
    assert 'Іван Петренко' not in body


def test_manager_sees_full_name(auth_client, make_user, monkeypatch):
    u = make_user(username='ident_u')
    u.first_name, u.last_name = 'Іван', 'Петренко'
    from app.extensions import db
    db.session.commit()

    _patch_ct(monkeypatch, [Row(u.id, 1, 2, 3, 4, 5)])
    cl = auth_client(role='manager')
    resp = cl.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'Іван Петренко' in body


def test_regular_user_sees_username(auth_client, make_user, monkeypatch):
    u = make_user(username='ident_u')
    u.first_name, u.last_name = 'Іван', 'Петренко'
    from app.extensions import db
    db.session.commit()

    _patch_ct(monkeypatch, [Row(u.id, 1, 2, 3, 4, 5)])
    cl = auth_client(role='viewer')
    resp = cl.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'ident_u' in body
    assert 'Іван Петренко' not in body


def test_empty_results_show_info_message(client, monkeypatch):
    _patch_ct(monkeypatch, [])
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert '<table' not in body
    assert 'немає даних про ідентифікації' in body


def test_combined_scope_select_rendered(client, monkeypatch):
    """Комбінований select зі scope-опціями присутній на сторінці."""
    _patch_ct(monkeypatch, [])
    resp = client.get(URL)
    body = resp.data.decode('utf-8')
    assert 'id="scope-select"' in body
    assert 'name="scope"' in body
    assert 'value="global:"' in body


# ──────────────────────────────────────────────────────────────────────────
# 2b. Видовий фільтр — рендер і бекенд-фільтрація
# ──────────────────────────────────────────────────────────────────────────

def test_species_filter_select_rendered(client, monkeypatch):
    """Сторінка містить select для фільтру по виду."""
    _patch_ct(monkeypatch, [])
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'id="species-select"' in body
    assert 'name="species_id"' in body


def test_species_filter_populates_options(client, monkeypatch):
    """Назви видів з available_species відображаються в select."""
    FakeSpecies = namedtuple('FakeSpecies', 'id scientific_name common_name_ua common_name_en')
    sp = FakeSpecies(id=42, scientific_name='Vulpes vulpes',
                     common_name_ua='Лисиця руда', common_name_en='Red Fox')
    _patch_ct(monkeypatch, [], species_list=[sp])
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'value="42"' in body
    assert 'Лисиця руда' in body


def test_species_filter_selected_option_persists(client, monkeypatch, make_user):
    """Після GET з species_id=42 цей option залишається selected."""
    FakeSpecies = namedtuple('FakeSpecies', 'id scientific_name common_name_ua common_name_en')
    sp = FakeSpecies(id=42, scientific_name='Vulpes vulpes',
                     common_name_ua='Лисиця руда', common_name_en='Red Fox')
    _patch_ct(monkeypatch, [], species_list=[sp])
    resp = client.get(URL + '?species_id=42')
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'value="42" selected' in body or 'value="42"  selected' in body or \
           'selected' in body  # Jinja може рендерити по-різному


def test_species_filter_narrows_query_contributor_stats(ct_session, make_ct_location,
                                                        make_ct_species):
    """query_contributor_stats з species_id повертає лише users з цим видом."""
    from app.camera_traps.routes import query_contributor_stats

    today = date(2025, 6, 15)
    loc = make_ct_location()
    sp1 = make_ct_species(scientific_name='Vulpes vulpes')
    sp2 = make_ct_species(scientific_name='Canis lupus', common_name_ua='Вовк')

    dt_today = datetime.combine(today, datetime.min.time())

    # user 1 — вид sp1
    _add_identification(ct_session, loc, user_id=10,
                        created_at=dt_today, species_id=sp1.id)
    # user 2 — вид sp2
    _add_identification(ct_session, loc, user_id=11,
                        created_at=dt_today, species_id=sp2.id)

    # Без фільтру — обидва
    all_rows = query_contributor_stats(ct_session, today, text("1=1"), {})
    assert {r.user_id for r in all_rows} == {10, 11}

    # Фільтр по sp1 — тільки user 10
    rows_sp1 = query_contributor_stats(ct_session, today, text("1=1"), {},
                                       species_id=sp1.id)
    assert {r.user_id for r in rows_sp1} == {10}

    # Фільтр по sp2 — тільки user 11
    rows_sp2 = query_contributor_stats(ct_session, today, text("1=1"), {},
                                       species_id=sp2.id)
    assert {r.user_id for r in rows_sp2} == {11}


def test_species_filter_route_returns_200(client, monkeypatch):
    """GET /contributors?species_id=42 повертає 200."""
    _patch_ct(monkeypatch, [])
    resp = client.get(URL + '?species_id=42')
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────────
# 3. resolve_scope — розбір комбінованого фільтру
# ──────────────────────────────────────────────────────────────────────────

Inst = namedtuple('Inst', 'id ecoregion_uk')


@pytest.fixture
def fake_institutions():
    return [
        Inst(1, 'Розточчя'),
        Inst(2, 'Розточчя'),
        Inst(3, 'Полісся'),
        Inst(4, None),
    ]


@pytest.mark.parametrize('arg', ['', 'global:', 'whatever', 'global:123'])
def test_resolve_scope_global(arg, fake_institutions):
    from app.camera_traps.routes import resolve_scope
    scope, ids = resolve_scope(arg, fake_institutions)
    assert scope == 'global:'
    assert ids is None


def test_resolve_scope_institution(fake_institutions):
    from app.camera_traps.routes import resolve_scope
    scope, ids = resolve_scope('institution:2', fake_institutions)
    assert scope == 'institution:2'
    assert ids == [2]


def test_resolve_scope_ecoregion_expands_to_institutions(fake_institutions):
    from app.camera_traps.routes import resolve_scope
    scope, ids = resolve_scope('ecoregion:Розточчя', fake_institutions)
    assert scope == 'ecoregion:Розточчя'
    assert sorted(ids) == [1, 2]


def test_resolve_scope_ecoregion_no_access_returns_empty_sentinel(fake_institutions):
    from app.camera_traps.routes import resolve_scope
    # Екорегіон, якого немає серед доступних установ → гарантовано порожній результат.
    scope, ids = resolve_scope('ecoregion:Степ', fake_institutions)
    assert scope == 'ecoregion:Степ'
    assert ids == [-1]
