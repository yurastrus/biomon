"""
Тести для сторінки та API аналізу поведінки тварин.

Покриває:
  - GET /<lang>/camera-traps/analysis/behavior
        (behavior_analysis) — доступ, шаблон, список видів
  - GET /<lang>/camera-traps/api/behavior/data
        (api_behavior_data)  — структура відповіді, бізнес-логіка,
        фільтри, обробка помилок
  - GET /<lang>/camera-traps/api/behavior/species-with-behaviors
        (api_behavior_species) — вміст, мова, структура JSON

Запуск:
    venv/Scripts/python -m unittest tests.test_behavior_analysis -v
"""

import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


# ════════════════════════════════════════════════════════════════════════════
# Допоміжні функції
# ════════════════════════════════════════════════════════════════════════════

def _login(client, user_id):
    """Встановлює Flask-Login сесію без HTTP-запиту."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


# ── Mock-об'єкти ────────────────────────────────────────────────────────────

def _species(id=1, name_ua='Вовк звичайний', name_en='Gray wolf',
             scientific='Canis lupus', active=True):
    s = MagicMock()
    s.id = id
    s.common_name_ua = name_ua
    s.common_name_en = name_en
    s.scientific_name = scientific
    s.is_active = active
    return s


def _biotope(id=1, name_ua='Ліс', name_en='Forest'):
    b = MagicMock()
    b.id = id
    b.name_ua = name_ua
    b.name_en = name_en
    return b


def _ident(id=1):
    i = MagicMock()
    i.id = id
    return i


def _behavior_row(id=1, name_ua='Годування', name_en='Feeding', obs_count=5):
    """Симулює рядок SQLAlchemy з агрегованим полем obs_count."""
    return SimpleNamespace(
        id=id, name_ua=name_ua, name_en=name_en, obs_count=obs_count
    )


def _seasonal_row(month=1, behavior_id=1, name_ua='Годування',
                  name_en='Feeding', obs_count=3):
    return SimpleNamespace(
        month=month, behavior_id=behavior_id,
        name_ua=name_ua, name_en=name_en, obs_count=obs_count,
    )


def _qty_row(qty=2, observation_id=1):
    return SimpleNamespace(qty=qty, observation_id=observation_id)


# ── Mock-сесії ───────────────────────────────────────────────────────────────

def _make_page_session(species=(), biotopes=(), viewer_species=None):
    """Mock ct_session для behavior_analysis (сторінка).

    Порядок викликів:
      1. Biotope  → .order_by().all()
      2. Species  → .join().join().filter()[.filter()].distinct().order_by().all()

    viewer_species: якщо передано — повертається по шляху з додатковим
                    .filter() (для звичайних юзерів, Species.id > 0).
                    Якщо None — обидва шляхи повертають species.
    """
    mock_session = MagicMock()
    call_idx = [0]

    def _query(*args):
        q = MagicMock()
        idx = call_idx[0]
        call_idx[0] += 1
        if idx == 0:
            q.order_by.return_value.all.return_value = list(biotopes)
        else:
            species_filter = (
                q.join.return_value
                 .join.return_value
                 .filter.return_value
            )
            # Шлях адмін/менеджер: без додаткового filter()
            (species_filter
               .distinct.return_value
               .order_by.return_value
               .all.return_value) = list(species)
            # Шлях звичайний юзер: з filter(Species.id > 0)
            _viewer = viewer_species if viewer_species is not None else species
            (species_filter
               .filter.return_value
               .distinct.return_value
               .order_by.return_value
               .all.return_value) = list(_viewer)
        return q

    mock_session.query.side_effect = _query
    return mock_session


def _make_data_session(identifications=(), behavior_rows=(),
                       seasonal_rows=(), qty_rows=(), tagged_count=0):
    """Mock ct_session для api_behavior_data.

    Порядок викликів:
      1. Identification (base_q)  3 joins → filter → params → all
         (та ж chain + ще join → filter → all для біотопу)
      2. BehaviorType distribution  3 joins → filter → group_by → order_by → all
      3. Seasonal rows              3 joins → filter → group_by → order_by → all
      4. Group size (qty)           1 join  → filter → group_by → all
      5. Tagged count               filter  → scalar

    Якщо identifications=() — маршрут повертає рано; запити 2-5 не виконуються.
    """
    mock_session = MagicMock()
    call_idx = [0]

    def _query(*args):
        q = MagicMock()
        idx = call_idx[0]
        call_idx[0] += 1

        if idx == 0:
            # base_q: 3 joins → filter → params → all
            base_end = (q.join.return_value
                          .join.return_value
                          .join.return_value
                          .filter.return_value
                          .params.return_value)
            base_end.all.return_value = list(identifications)
            # також підтримуємо шлях з біотопом: params → join → filter → all
            (base_end.join.return_value
                     .filter.return_value
                     .all.return_value) = list(identifications)

        elif idx == 1:
            # behavior distribution: 3 joins → filter → group_by → order_by → all
            (q.join.return_value
               .join.return_value
               .join.return_value
               .filter.return_value
               .group_by.return_value
               .order_by.return_value
               .all.return_value) = list(behavior_rows)

        elif idx == 2:
            # seasonal: 3 joins → filter → group_by → order_by → all
            (q.join.return_value
               .join.return_value
               .join.return_value
               .filter.return_value
               .group_by.return_value
               .order_by.return_value
               .all.return_value) = list(seasonal_rows)

        elif idx == 3:
            # qty: 1 join → filter → group_by → all
            (q.join.return_value
               .filter.return_value
               .group_by.return_value
               .all.return_value) = list(qty_rows)

        else:
            # tagged_count: filter → scalar
            q.filter.return_value.scalar.return_value = tagged_count

        return q

    mock_session.query.side_effect = _query
    return mock_session


def _make_species_session(species=()):
    """Mock ct_session для api_behavior_species."""
    mock_session = MagicMock()
    (mock_session.query.return_value
       .join.return_value
       .join.return_value
       .filter.return_value
       .distinct.return_value
       .order_by.return_value
       .all.return_value) = list(species)
    return mock_session


# ════════════════════════════════════════════════════════════════════════════
# Базовий клас тестів
# ════════════════════════════════════════════════════════════════════════════

class BehaviorBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock(),
        )
        cls._ct_patcher.start()
        from app import create_app
        cls.app = create_app('testing')
        cls.app.config['GEOSERVER_URL'] = 'http://test-geoserver'

    @classmethod
    def tearDownClass(cls):
        cls._ct_patcher.stop()
        os.environ.pop('DATABASE_URL', None)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app.extensions import db
        db.create_all()
        self._seed(db)
        self.client = self.app.test_client()

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self, db):
        from app.extensions import bcrypt
        from app.models import User, Role, Institution, UserInstitution

        r_admin   = Role(name='admin')
        r_manager = Role(name='manager')
        r_viewer  = Role(name='viewer')
        db.session.add_all([r_admin, r_manager, r_viewer])
        db.session.flush()

        self.inst_a = Institution(
            name_uk='Заповідник А', name_en='Reserve A', code='res_a',
            ecoregion_uk='Розточчя', ecoregion_en='Roztochya',
        )
        self.inst_b = Institution(
            name_uk='Заповідник Б', name_en='Reserve B', code='res_b',
            ecoregion_uk='Полісся', ecoregion_en='Polissia',
        )
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('test').decode('utf-8')

        self.admin = User(username='admin_u', password_hash=pw)
        self.admin.roles.append(r_admin)
        db.session.add(self.admin)

        self.manager = User(username='manager_u', password_hash=pw)
        self.manager.roles.append(r_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.manager)

        self.viewer = User(username='viewer_u', password_hash=pw)
        self.viewer.roles.append(r_viewer)
        db.session.add(self.viewer)

        db.session.commit()

    # ── HTTP-хелпери ─────────────────────────────────────────────────────────

    def _get(self, url, user_id=None, ct_session=None):
        if user_id:
            _login(self.client, user_id)
        if ct_session is not None:
            with patch('app.camera_traps.routes.get_ct_session',
                       return_value=ct_session), \
                 patch('app.camera_traps.routes.close_ct_session'):
                return self.client.get(url)
        return self.client.get(url)

    def _get_json(self, url, user_id=None, ct_session=None):
        resp = self._get(url, user_id=user_id, ct_session=ct_session)
        return resp, json.loads(resp.data)


# ════════════════════════════════════════════════════════════════════════════
# 1. СТОРІНКА — ДОСТУП
# ════════════════════════════════════════════════════════════════════════════

class TestBehaviorPageAccess(BehaviorBase):
    """GET /analysis/behavior — перевірка доступу для різних ролей."""

    URL = '/uk/camera-traps/analysis/behavior'

    def test_anonymous_gets_200(self):
        """Сторінка публічна — анонімний користувач отримує 200."""
        resp = self._get(self.URL, ct_session=_make_page_session())
        self.assertEqual(resp.status_code, 200)

    def test_viewer_gets_200(self):
        resp = self._get(self.URL, user_id=self.viewer.id,
                         ct_session=_make_page_session())
        self.assertEqual(resp.status_code, 200)

    def test_manager_gets_200(self):
        resp = self._get(self.URL, user_id=self.manager.id,
                         ct_session=_make_page_session())
        self.assertEqual(resp.status_code, 200)

    def test_admin_gets_200(self):
        resp = self._get(self.URL, user_id=self.admin.id,
                         ct_session=_make_page_session())
        self.assertEqual(resp.status_code, 200)

    def test_english_url_gets_200(self):
        resp = self._get('/en/camera-traps/analysis/behavior',
                         ct_session=_make_page_session())
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════════════════════
# 2. СТОРІНКА — КОНТЕНТ
# ════════════════════════════════════════════════════════════════════════════

class TestBehaviorPageContent(BehaviorBase):
    """HTML-контент сторінки behavior_analysis."""

    URL = '/uk/camera-traps/analysis/behavior'

    def test_page_title_in_html(self):
        resp = self._get(self.URL, ct_session=_make_page_session())
        self.assertIn('Аналіз поведінки'.encode(), resp.data)

    def test_species_name_appears_in_select(self):
        """Вид з поведінковими тегами відображається в фільтрі."""
        sess = _make_page_session(
            species=[_species(1, name_ua='Вовк звичайний', scientific='Canis lupus')],
        )
        resp = self._get(self.URL, ct_session=sess)
        self.assertIn('Вовк звичайний'.encode(), resp.data)

    def test_multiple_species_all_appear(self):
        sess = _make_page_session(
            species=[
                _species(1, name_ua='Вовк звичайний'),
                _species(2, name_ua='Лисиця звичайна'),
                _species(3, name_ua='Козуля'),
            ],
        )
        resp = self._get(self.URL, ct_session=sess)
        self.assertIn('Вовк звичайний'.encode(), resp.data)
        self.assertIn('Лисиця звичайна'.encode(), resp.data)
        self.assertIn('Козуля'.encode(), resp.data)

    def test_empty_species_list_renders_without_error(self):
        """Якщо видів з поведінками немає — сторінка рендериться без помилки."""
        resp = self._get(self.URL, ct_session=_make_page_session(species=[]))
        self.assertEqual(resp.status_code, 200)

    def test_biotope_appears_in_filter(self):
        sess = _make_page_session(
            biotopes=[_biotope(1, 'Мішаний ліс', 'Mixed forest')],
        )
        resp = self._get(self.URL, ct_session=sess)
        self.assertIn('Мішаний ліс'.encode(), resp.data)

    def test_institution_appears_for_manager(self):
        """Менеджер бачить свою установу у фільтрі."""
        resp = self._get(self.URL, user_id=self.manager.id,
                         ct_session=_make_page_session())
        self.assertIn('Заповідник А'.encode(), resp.data)

    def test_manager_does_not_see_foreign_institution(self):
        """Менеджер НЕ бачить установу, до якої не належить."""
        resp = self._get(self.URL, user_id=self.manager.id,
                         ct_session=_make_page_session())
        self.assertNotIn('Заповідник Б'.encode(), resp.data)

    def test_admin_sees_all_institutions(self):
        resp = self._get(self.URL, user_id=self.admin.id,
                         ct_session=_make_page_session())
        self.assertIn('Заповідник А'.encode(), resp.data)
        self.assertIn('Заповідник Б'.encode(), resp.data)

    def test_anonymous_sees_no_institution_filter(self):
        """Анонімний — інституцій немає, список порожній."""
        resp = self._get(self.URL, ct_session=_make_page_session())
        # Немає жодної option з установою, але форма рендериться
        self.assertNotIn('Заповідник А'.encode(), resp.data)
        self.assertNotIn('Заповідник Б'.encode(), resp.data)

    def test_ecoregion_appears_for_manager_with_institution(self):
        """Менеджер бачить екорегіон своєї установи."""
        resp = self._get(self.URL, user_id=self.manager.id,
                         ct_session=_make_page_session())
        self.assertIn('Розточчя'.encode(), resp.data)

    # ── Фільтрація видів за роллю ─────────────────────────────────────────────

    def test_admin_sees_negative_id_species(self):
        """Адмін бачить «технічні» види (id < 0) — вони повертаються без extra filter."""
        technical = _species(id=-1, name_ua='Автомобіль')
        normal    = _species(id=1,  name_ua='Козуля')
        sess = _make_page_session(species=[technical, normal])
        resp = self._get(self.URL, user_id=self.admin.id, ct_session=sess)
        self.assertIn('Автомобіль'.encode(), resp.data)
        self.assertIn('Козуля'.encode(), resp.data)

    def test_manager_sees_negative_id_species(self):
        """Менеджер (установа є) теж бачить технічні види."""
        technical = _species(id=-1, name_ua='Мотоцикл')
        normal    = _species(id=2,  name_ua='Лисиця')
        sess = _make_page_session(species=[technical, normal])
        resp = self._get(self.URL, user_id=self.manager.id, ct_session=sess)
        self.assertIn('Мотоцикл'.encode(), resp.data)
        self.assertIn('Лисиця'.encode(), resp.data)

    def test_viewer_gets_filtered_species_list(self):
        """Звичайний юзер (без установ) бачить лише види з id > 0."""
        all_species = [_species(id=-1, name_ua='Людина'),
                       _species(id=1,  name_ua='Козуля')]
        filtered    = [_species(id=1,  name_ua='Козуля')]
        # admin-шлях повертає all_species; viewer-шлях (extra filter) — filtered
        sess = _make_page_session(species=all_species, viewer_species=filtered)
        resp = self._get(self.URL, user_id=self.viewer.id, ct_session=sess)
        self.assertNotIn('Людина'.encode(), resp.data)
        self.assertIn('Козуля'.encode(), resp.data)

    # ── Решта ────────────────────────────────────────────────────────────────

    def test_apply_button_in_html(self):
        resp = self._get(self.URL, ct_session=_make_page_session())
        self.assertIn(b'apply-btn', resp.data)

    def test_species_select_element_present(self):
        resp = self._get(self.URL, ct_session=_make_page_session())
        self.assertIn(b'species-select', resp.data)

    def test_date_inputs_have_default_values(self):
        """Поля дат мають дефолтні значення (start_date передається з бекенду)."""
        resp = self._get(self.URL, ct_session=_make_page_session())
        # start_date='2020-08-01' вбудований у value=""
        self.assertIn(b'2020-08-01', resp.data)

    def test_scientific_name_appended(self):
        """Наукова назва додається в дужках після загальної."""
        sess = _make_page_session(
            species=[_species(1, name_ua='Вовк', scientific='Canis lupus')],
        )
        resp = self._get(self.URL, ct_session=sess)
        self.assertIn(b'Canis lupus', resp.data)

    def test_species_without_scientific_name(self):
        """Вид без наукової назви — тільки загальна (без дужок)."""
        sess = _make_page_session(
            species=[_species(1, name_ua='Невідомий', scientific=None)],
        )
        resp = self._get(self.URL, ct_session=sess)
        self.assertIn('Невідомий'.encode(), resp.data)
        self.assertNotIn(b'None', resp.data)


# ════════════════════════════════════════════════════════════════════════════
# 3. API /api/behavior/data — ПОМИЛКИ ВВОДУ
# ════════════════════════════════════════════════════════════════════════════

class TestApiBehaviorDataErrors(BehaviorBase):
    """api_behavior_data — обробка некоректних запитів."""

    URL = '/uk/camera-traps/api/behavior/data'

    def test_missing_species_id_returns_400(self):
        resp, body = self._get_json(self.URL,
                                    ct_session=_make_data_session())
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', body)

    def test_species_id_zero_returns_400(self):
        resp, body = self._get_json(self.URL + '?species_id=0',
                                    ct_session=_make_data_session())
        self.assertEqual(resp.status_code, 400)

    def test_non_numeric_species_id_returns_400(self):
        resp, body = self._get_json(self.URL + '?species_id=abc',
                                    ct_session=_make_data_session())
        self.assertEqual(resp.status_code, 400)


# ════════════════════════════════════════════════════════════════════════════
# 4. API /api/behavior/data — СТРУКТУРА ВІДПОВІДІ
# ════════════════════════════════════════════════════════════════════════════

class TestApiBehaviorDataStructure(BehaviorBase):
    """api_behavior_data — перевірка структури JSON."""

    URL = '/uk/camera-traps/api/behavior/data?species_id=1'

    def test_response_has_all_top_level_keys(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[_behavior_row()],
        )
        _, body = self._get_json(self.URL, ct_session=sess)
        self.assertIn('behavior_distribution', body)
        self.assertIn('seasonal_behaviors', body)
        self.assertIn('group_size_histogram', body)
        self.assertIn('total_identifications', body)
        self.assertIn('untagged_count', body)

    def test_empty_result_when_no_identifications(self):
        """Якщо ідентифікацій немає — всі масиви порожні, total=0."""
        sess = _make_data_session(identifications=[])
        _, body = self._get_json(self.URL, ct_session=sess)
        self.assertEqual(body['behavior_distribution'], [])
        self.assertEqual(body['seasonal_behaviors'], [])
        self.assertEqual(body['group_size_histogram'], [])
        self.assertEqual(body['total_identifications'], 0)

    def test_behavior_distribution_item_keys(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[_behavior_row(id=2, name_ua='Годування',
                                         name_en='Feeding', obs_count=7)],
        )
        _, body = self._get_json(self.URL, ct_session=sess)
        items = body['behavior_distribution']
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn('behavior_id', item)
        self.assertIn('label', item)
        self.assertIn('count', item)

    def test_behavior_distribution_values(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[_behavior_row(id=3, name_ua='Переміщення',
                                         name_en='Movement', obs_count=12)],
        )
        _, body = self._get_json(self.URL, ct_session=sess)
        item = body['behavior_distribution'][0]
        self.assertEqual(item['behavior_id'], 3)
        self.assertEqual(item['label'], 'Переміщення')  # uk мова (URL /uk/)
        self.assertEqual(item['count'], 12)

    def test_seasonal_item_keys(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            seasonal_rows=[_seasonal_row(month=9, behavior_id=1,
                                          name_ua='Маркування', obs_count=4)],
        )
        _, body = self._get_json(self.URL, ct_session=sess)
        items = body['seasonal_behaviors']
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn('month', item)
        self.assertIn('behavior_id', item)
        self.assertIn('label', item)
        self.assertIn('count', item)

    def test_seasonal_month_is_integer(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            seasonal_rows=[_seasonal_row(month=6)],
        )
        _, body = self._get_json(self.URL, ct_session=sess)
        item = body['seasonal_behaviors'][0]
        self.assertIsInstance(item['month'], int)
        self.assertEqual(item['month'], 6)

    def test_group_size_item_keys(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            qty_rows=[_qty_row(qty=3, observation_id=1)],
        )
        _, body = self._get_json(self.URL, ct_session=sess)
        items = body['group_size_histogram']
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn('quantity', item)
        self.assertIn('frequency', item)

    def test_total_identifications_matches_count(self):
        idents = [_ident(i) for i in range(1, 6)]  # 5 ідентифікацій
        sess = _make_data_session(identifications=idents)
        _, body = self._get_json(self.URL, ct_session=sess)
        self.assertEqual(body['total_identifications'], 5)

    def test_untagged_count_in_response(self):
        """untagged_count = total - tagged_count."""
        idents = [_ident(i) for i in range(1, 6)]  # 5 ідентифікацій
        # 3 з них мають теги (tagged_count=3) → untagged=2
        sess = _make_data_session(identifications=idents, tagged_count=3)
        _, body = self._get_json(self.URL, ct_session=sess)
        self.assertEqual(body['untagged_count'], 2)

    def test_untagged_count_zero_when_all_tagged(self):
        """Якщо всі ідентифікації мають теги — untagged_count=0."""
        idents = [_ident(i) for i in range(1, 4)]
        sess = _make_data_session(identifications=idents, tagged_count=3)
        _, body = self._get_json(self.URL, ct_session=sess)
        self.assertEqual(body['untagged_count'], 0)

    def test_content_type_is_json(self):
        sess = _make_data_session(identifications=[])
        resp = self._get(self.URL, ct_session=sess)
        self.assertIn('application/json', resp.content_type)


# ════════════════════════════════════════════════════════════════════════════
# 5. API /api/behavior/data — БІЗНЕС-ЛОГІКА
# ════════════════════════════════════════════════════════════════════════════

class TestApiBehaviorDataLogic(BehaviorBase):
    """api_behavior_data — бізнес-логіка: агрегація, фільтри, мова."""

    BASE = '/uk/camera-traps/api/behavior/data?species_id=1'
    BASE_EN = '/en/camera-traps/api/behavior/data?species_id=1'

    # ── Мова ─────────────────────────────────────────────────────────────────

    def test_behavior_label_ukrainian_by_default(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[_behavior_row(name_ua='Годування', name_en='Feeding')],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        self.assertEqual(body['behavior_distribution'][0]['label'], 'Годування')

    def test_behavior_label_english_when_lang_en(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[_behavior_row(name_ua='Годування', name_en='Feeding')],
        )
        _, body = self._get_json(self.BASE_EN, ct_session=sess)
        self.assertEqual(body['behavior_distribution'][0]['label'], 'Feeding')

    def test_seasonal_label_ukrainian(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            seasonal_rows=[_seasonal_row(name_ua='Маркування', name_en='Marking')],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        self.assertEqual(body['seasonal_behaviors'][0]['label'], 'Маркування')

    def test_seasonal_label_english(self):
        sess = _make_data_session(
            identifications=[_ident(1)],
            seasonal_rows=[_seasonal_row(name_ua='Маркування', name_en='Marking')],
        )
        _, body = self._get_json(self.BASE_EN, ct_session=sess)
        self.assertEqual(body['seasonal_behaviors'][0]['label'], 'Marking')

    def test_behavior_falls_back_to_ua_when_en_missing(self):
        """Якщо name_en = None або порожній — використовується name_ua."""
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[_behavior_row(name_ua='Відпочинок', name_en=None)],
        )
        _, body = self._get_json(self.BASE_EN, ct_session=sess)
        self.assertEqual(body['behavior_distribution'][0]['label'], 'Відпочинок')

    # ── Гістограма розміру групи ──────────────────────────────────────────────

    def test_group_histogram_single_entry(self):
        """1 спостереження з кількістю 2 → histogram [{quantity:2, frequency:1}]."""
        sess = _make_data_session(
            identifications=[_ident(1)],
            qty_rows=[_qty_row(qty=2, observation_id=10)],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        hist = body['group_size_histogram']
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]['quantity'], 2)
        self.assertEqual(hist[0]['frequency'], 1)

    def test_group_histogram_multiple_same_quantity(self):
        """Два спостереження по 2 особини → frequency=2."""
        sess = _make_data_session(
            identifications=[_ident(1)],
            qty_rows=[
                _qty_row(qty=2, observation_id=10),
                _qty_row(qty=2, observation_id=11),
            ],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        hist = body['group_size_histogram']
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]['quantity'], 2)
        self.assertEqual(hist[0]['frequency'], 2)

    def test_group_histogram_mixed_quantities(self):
        """Кілька різних кількостей → декілька записів, відсортовані за qty."""
        sess = _make_data_session(
            identifications=[_ident(1)],
            qty_rows=[
                _qty_row(qty=1, observation_id=1),
                _qty_row(qty=3, observation_id=2),
                _qty_row(qty=1, observation_id=3),
                _qty_row(qty=5, observation_id=4),
            ],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        hist = body['group_size_histogram']
        quantities = [h['quantity'] for h in hist]
        # Відсортовані за зростанням
        self.assertEqual(quantities, sorted(quantities))
        # Qty=1 зустрічалась двічі
        entry_1 = next(h for h in hist if h['quantity'] == 1)
        self.assertEqual(entry_1['frequency'], 2)
        # Qty=3 і 5 — по одному разу
        entry_3 = next(h for h in hist if h['quantity'] == 3)
        self.assertEqual(entry_3['frequency'], 1)

    def test_group_histogram_empty_when_no_qty(self):
        """Якщо немає qty-рядків — histogram порожній."""
        sess = _make_data_session(
            identifications=[_ident(1)],
            qty_rows=[],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        self.assertEqual(body['group_size_histogram'], [])

    # ── Поведінки — множинні рядки ───────────────────────────────────────────

    def test_multiple_behaviors_all_returned(self):
        behaviors = [
            _behavior_row(id=1, name_ua='Годування',    obs_count=10),
            _behavior_row(id=2, name_ua='Переміщення',  obs_count=6),
            _behavior_row(id=3, name_ua='Відпочинок',   obs_count=2),
        ]
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=behaviors,
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        self.assertEqual(len(body['behavior_distribution']), 3)

    def test_multiple_seasonal_rows_all_returned(self):
        seasonals = [
            _seasonal_row(month=1, behavior_id=1, obs_count=2),
            _seasonal_row(month=6, behavior_id=1, obs_count=5),
            _seasonal_row(month=9, behavior_id=2, obs_count=3),
        ]
        sess = _make_data_session(
            identifications=[_ident(1)],
            seasonal_rows=seasonals,
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        self.assertEqual(len(body['seasonal_behaviors']), 3)

    # ── Фільтри ───────────────────────────────────────────────────────────────

    def test_invalid_date_format_does_not_crash(self):
        """Некоректна дата → дефолти, 200 OK."""
        url = self.BASE + '&start_date=not-a-date&end_date=also-bad'
        sess = _make_data_session(identifications=[])
        resp, body = self._get_json(url, ct_session=sess)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('total_identifications', body)

    def test_institution_filter_parameter_accepted(self):
        """Параметр institution_id не викликає помилки."""
        url = self.BASE + f'&institution_id={self.inst_a.id}'
        sess = _make_data_session(identifications=[])
        resp, _ = self._get_json(url, ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    def test_ecoregion_filter_maps_to_institution(self):
        """Параметр ecoregion знаходить установу через Institution.query."""
        url = self.BASE + '&ecoregion=Розточчя'
        sess = _make_data_session(identifications=[])
        resp, body = self._get_json(url, user_id=self.manager.id,
                                    ct_session=sess)
        # Інституція inst_a має ecoregion_uk='Розточчя' — запит не падає
        self.assertEqual(resp.status_code, 200)

    def test_biotope_filter_parameter_accepted(self):
        """Параметр biotope_id не викликає помилки."""
        url = self.BASE + '&biotope_id=1'
        sess = _make_data_session(identifications=[])
        resp, _ = self._get_json(url, ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    def test_unknown_ecoregion_returns_empty(self):
        """Неіснуючий екорегіон → Institution.query повертає [] → порожній результат."""
        url = self.BASE + '&ecoregion=НеіснуючийЕкорегіон'
        sess = _make_data_session(identifications=[])
        resp, body = self._get_json(url, ct_session=sess)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body['total_identifications'], 0)

    def test_multiple_institution_ids_accepted(self):
        url = self.BASE + f'&institution_id={self.inst_a.id},{self.inst_b.id}'
        sess = _make_data_session(identifications=[])
        resp, _ = self._get_json(url, ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    # ── Права доступу ─────────────────────────────────────────────────────────

    def test_anonymous_can_call_api(self):
        """API доступне без авторизації."""
        sess = _make_data_session(identifications=[])
        resp, body = self._get_json(self.BASE, ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_user_can_call_api(self):
        sess = _make_data_session(identifications=[])
        resp, body = self._get_json(self.BASE, user_id=self.manager.id,
                                    ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_call_api(self):
        sess = _make_data_session(identifications=[])
        resp, body = self._get_json(self.BASE, user_id=self.admin.id,
                                    ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    # ── Сезонні дані — валідність місяців ─────────────────────────────────────

    def test_seasonal_month_values_are_1_to_12(self):
        """Місяці мають бути цілими числами 1–12."""
        seasonals = [
            _seasonal_row(month=m, behavior_id=1)
            for m in [1, 3, 6, 9, 12]
        ]
        sess = _make_data_session(
            identifications=[_ident(1)],
            seasonal_rows=seasonals,
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        months = [r['month'] for r in body['seasonal_behaviors']]
        for m in months:
            self.assertIn(m, range(1, 13))

    def test_behavior_counts_are_positive(self):
        """obs_count у behavior_distribution завжди > 0."""
        sess = _make_data_session(
            identifications=[_ident(1)],
            behavior_rows=[
                _behavior_row(obs_count=1),
                _behavior_row(id=2, name_ua='Переміщення',
                              name_en='Movement', obs_count=15),
            ],
        )
        _, body = self._get_json(self.BASE, ct_session=sess)
        for item in body['behavior_distribution']:
            self.assertGreater(item['count'], 0)


# ════════════════════════════════════════════════════════════════════════════
# 6. API /api/behavior/species-with-behaviors — СТРУКТУРА І МОВА
# ════════════════════════════════════════════════════════════════════════════

class TestApiBehaviorSpecies(BehaviorBase):
    """api_behavior_species — список видів з поведінками."""

    URL_UK = '/uk/camera-traps/api/behavior/species-with-behaviors'
    URL_EN = '/en/camera-traps/api/behavior/species-with-behaviors'

    def test_returns_200(self):
        sess = _make_species_session()
        resp = self._get(self.URL_UK, ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    def test_content_type_is_json(self):
        sess = _make_species_session()
        resp = self._get(self.URL_UK, ct_session=sess)
        self.assertIn('application/json', resp.content_type)

    def test_returns_list(self):
        sess = _make_species_session()
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertIsInstance(body, list)

    def test_empty_when_no_species_with_behaviors(self):
        sess = _make_species_session(species=[])
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertEqual(body, [])

    def test_item_has_id_and_text(self):
        sess = _make_species_session(
            species=[SimpleNamespace(
                id=1, common_name_ua='Вовк', common_name_en='Wolf',
                scientific_name='Canis lupus',
            )],
        )
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertEqual(len(body), 1)
        self.assertIn('id', body[0])
        self.assertIn('text', body[0])

    def test_ukrainian_name_for_uk_lang(self):
        sess = _make_species_session(
            species=[SimpleNamespace(
                id=1, common_name_ua='Вовк', common_name_en='Wolf',
                scientific_name='Canis lupus',
            )],
        )
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertIn('Вовк', body[0]['text'])

    def test_english_name_for_en_lang(self):
        sess = _make_species_session(
            species=[SimpleNamespace(
                id=1, common_name_ua='Вовк', common_name_en='Wolf',
                scientific_name='Canis lupus',
            )],
        )
        _, body = self._get_json(self.URL_EN, ct_session=sess)
        self.assertIn('Wolf', body[0]['text'])

    def test_scientific_name_included_in_text(self):
        sess = _make_species_session(
            species=[SimpleNamespace(
                id=42, common_name_ua='Козуля', common_name_en='Roe deer',
                scientific_name='Capreolus capreolus',
            )],
        )
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertIn('Capreolus capreolus', body[0]['text'])

    def test_id_matches_species_id(self):
        sess = _make_species_session(
            species=[SimpleNamespace(
                id=99, common_name_ua='Рись', common_name_en='Lynx',
                scientific_name='Lynx lynx',
            )],
        )
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertEqual(body[0]['id'], 99)

    def test_multiple_species_returned(self):
        species_data = [
            SimpleNamespace(id=i, common_name_ua=f'Вид {i}',
                            common_name_en=f'Species {i}',
                            scientific_name=f'Speciesus {i}')
            for i in range(1, 6)
        ]
        sess = _make_species_session(species=species_data)
        _, body = self._get_json(self.URL_UK, ct_session=sess)
        self.assertEqual(len(body), 5)

    def test_fallback_to_ua_name_when_en_absent(self):
        """Якщо common_name_en = None — використовується ua-назва."""
        sess = _make_species_session(
            species=[SimpleNamespace(
                id=1, common_name_ua='Борсук', common_name_en=None,
                scientific_name='Meles meles',
            )],
        )
        _, body = self._get_json(self.URL_EN, ct_session=sess)
        self.assertIn('Борсук', body[0]['text'])

    def test_anonymous_can_access_species_api(self):
        """API видів доступне без авторизації."""
        sess = _make_species_session()
        resp = self._get(self.URL_UK, ct_session=sess)
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_user_can_access_species_api(self):
        sess = _make_species_session()
        resp = self._get(self.URL_UK, user_id=self.viewer.id,
                         ct_session=sess)
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════════════════════
# 7. НАВІГАЦІЯ — посилання з dashboard
# ════════════════════════════════════════════════════════════════════════════

class TestBehaviorNavLink(BehaviorBase):
    """Перевіряє що посилання на сторінку поведінки є на dashboard."""

    def _dashboard_session(self):
        """Мінімальний mock для dashboard."""
        mock_session = MagicMock()
        # Всі query-ланцюжки повертають порожні списки
        q = MagicMock()
        q.scalar.return_value = 0
        q.all.return_value = []
        q.filter.return_value.scalar.return_value = 0
        q.filter.return_value.all.return_value = []
        q.join.return_value.filter.return_value.scalar.return_value = 0
        q.join.return_value.filter.return_value.all.return_value = []
        q.order_by.return_value.all.return_value = []
        q.join.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_session.query.return_value = q
        mock_session.execute.return_value.fetchall.return_value = []
        mock_session.execute.return_value.scalar.return_value = 0
        mock_session.execute.return_value.mappings.return_value.fetchall.return_value = []
        return mock_session

    def test_behavior_link_on_dashboard(self):
        _login(self.client, self.admin.id)
        with patch('app.camera_traps.routes.get_ct_session',
                   return_value=self._dashboard_session()), \
             patch('app.camera_traps.routes.close_ct_session'):
            resp = self.client.get('/uk/camera-traps/dashboard')
        # Dashboard повертає 200 з посиланням на /analysis/behavior
        if resp.status_code == 200:
            self.assertIn(b'/analysis/behavior', resp.data)


# ════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    unittest.main()
