# WORKLOG — biomon

> Note: entries from 2026-08-14 on are written in English per the global
> documentation-language rule; earlier entries stay in Ukrainian as written.

## 2026-08-14 — PAM export authorisation aligned with the rest of the system

### Symptom
User `vasylyna` (roles `analyst, ct_verifier, pam_verifier`, `can_export=true`
on 6 institutions) could not see the data-export card on `/uk/pam`, while the
same account exports camera-traps data fine.

### Root cause
PAM was the only module gating export on the `manager` role:
- `pam_home` computed `can_export = has_role('manager', 'roztochya_user')`.
  `has_role` expands the hierarchy downward only, so `analyst` never reaches
  `manager` → card hidden.
- `roztochya_user` does not exist in the `role` table at all → dead branch, so
  the gate was effectively manager-only (6 users) + admin.
- `pam_data_export` and `api_data_preview` carried `@role_required('manager')`.

This contradicted `EXPORT_ROLES = {analyst, manager, admin}`
(`app/admin/services.py:13`) — the set the admin panel uses to decide who may
be given `can_export` — and `ct_data_export`, which uses
`@role_required('analyst')` + `current_user.export_institutions`.

Two further defects found while tracing it:
- **`api_data_download` had no decorators at all** — no `@login_required`, no
  `@role_required`. Any authenticated user of any role could pull the CSV for
  their institutions by hitting the URL directly; anonymous callers got
  public-visibility rows (`get_occurrence_data` falls back to
  `visibility_level = 0`). This is why vasylyna could technically already
  download PAM data — the UI just offered no path to it.
- Export scoping used `current_user.institutions` (plain membership, 28 rows for
  vasylyna) and ignored the `can_export` flag entirely.

### Change (submodule `shared-pam`)
- New `has_pam_export_access()` helper in `routes.py`: `analyst` privilege level
  AND at least one `can_export` institution; admin unrestricted. Single source
  of truth for the three endpoints and the hub card.
- `pam_home`: `can_export=has_pam_export_access()`; dead `roztochya_user` gone.
- `pam_data_export`: `@role_required('analyst')` + access guard (flash+redirect),
  institution dropdown now built from `export_institutions`, not `institutions`.
- `api_data_preview`: `@role_required('analyst')` + guard → 403.
- `api_data_download`: `@login_required` + `@role_required('analyst')` + guard.
- `utils.get_occurrence_data`: access baseline switched from `institutions` to
  `export_institutions`; non-admin with an empty export scope now returns
  `{'data': [], 'total_count': 0}` instead of silently falling back to public
  locations.

### Decision
Per-institution `can_export` is authoritative for **everyone** except admin,
managers included. A manager who is a member of an institution without the flag
no longer sees or exports its data — deliberate, confirmed with the user; it
narrows what the 6 existing managers see.

### Tests
`tests/test_pam_data_export.py`: 34 → 51. Seed gained an analyst with
`can_export` on one of two institutions (the vasylyna case), an analyst with the
role but no flag, a `pam_verifier`, and a manager membership with
`can_export=False`. New coverage: hub card visibility per role, page/preview/
download authorisation (incl. anonymous and viewer on `data-download` — the
regression for the missing decorators), and `get_occurrence_data` baseline
using export institutions and short-circuiting on an empty scope.

Full suite: 1402 passed, 3 failed, 36 skipped. The 3 failures are in
`tests/test_pam_verification_priority.py` (`next verification segment: tuple
index out of range`, HTTP 500) and **pre-exist this change** — verified by
stashing the submodule edits and re-running. Not touched here.

### Next step
Deploy: commit `shared-pam` → bump the pointer in `biomon` → `git pull` +
reload gunicorn on prod. No migration, no data change. Separately: the
`test_pam_verification_priority` 500 needs its own fix.

## 2026-07-24 — Автопризначення біотопів у PAM (порт із CT)

Порт CT-фічі автопризначення біотопів у модуль PAM (сабмодуль `shared-pam`,
окрема `pam_db`). PAM структурно дзеркалить CT (locations lat/lon + geom,
biotopes, location_biotopes M2M), тож логіка перенесена майже 1:1.

- **`app/pam/biotope_autoassign.py`** — самодостатній порт (через `get_pam_engine`,
  колонки `location_id/lat/lon`; свій GEE-init на `GEE_SERVICE_ACCOUNT_KEY`;
  `frequencyHistogram` над `Point.buffer`; аддитивний `ON CONFLICT`;
  `start_async_assign` threading). tz-aware статус, stuck-cutoff рахується в SQL.
- **Адмін-хаб**: новий `GET /<lang>/pam/admin` + `pam_admin.html` (лише біотопна
  секція; старі адмін-дії PAM не чіпав — на потім). Посилання-картка додана в
  «Управління» на `pam_home.html` (admin-only). Роути: `POST .../auto-assign`
  (202/409/503), `GET .../status`. Повідомлення роуту — хардкод-українська
  (стиль PAM), рядки шаблону — через `_()` (домен pam).
- **Таблиці в pam_db**: `biotope_landcover_map` + generic `pam_calculation_log`
  (для статусу/polling). Створюються `ensure_schema()` ідемпотентно —
  **без committed init-скрипта** (на прохання користувача); DDL у докстрінгу модуля.
- **Сід (одноразово, напряму в pam_db)**: PAM має 16 біотопів, тож додано лише
  загальний **«Ліс»** (id 17); решта класів змаплено на наявні (20→Кущі, 30→Лука,
  40→C/г поля, 50→Населені пункти, 60→Скелі та урвища, 80→Озера та водосховища,
  90→Очерети). Мапінг керується лише в БД.
- **Верифікація**: 14 тестів (топ-N, gating, 503, admin-only) — pass; повний набір
  — pass. Наскрізь на pam_db: `gee_landcover_available()=True`, гістограми Розточчя
  логічні (ліс/стави), write-path `unnest`+`ON CONFLICT` (rollback-тест, 2 нові
  звʼязки). Масовий прогін по 42 точках без біотопів — НЕ запускав (кнопкою).



## 2026-07-15 — Автопризначення біотопів з лендковеру (Beta)

### Задача
894 з 926 локацій CT не мали біотопів (заповнюються вручну, треба знати, що на
точці). Через це ламались біотопні фільтри дашбордів. Потрібна кнопка в адмінці,
що автоматично призначає біотопи за аналізом лендковеру навколо точки.

### Рішення
- **Джерело:** ESA WorldCover v200 2021 (10 м) через GEE — той самий датасет, що
  й у модулі SDM, але SDM рахує частку класу; тут потрібна гістограма → дописав
  `frequencyHistogram` над `Point.buffer(radius)`.
- **Портативність:** модуль `biotope_autoassign.py` самодостатній (НЕ імпортує
  `app.sdm`, бо `camera_traps` — публічний сабмодуль shared-ct, що крутиться і в
  myproject). Ледачий `import ee`, свій singleton-init, читає той самий
  `GEE_SERVICE_ACCOUNT_KEY`.
- **Мапінг клас→біотоп:** окрема таблиця `biotope_landcover_map` (обрано замість
  колонки/хардкоду — редаговане, реюзабельне) + міні-редактор у адмінці.
- **Аддитивність:** `ON CONFLICT DO NOTHING` на `location_biotopes` — наявні
  біотопи не стираються.
- **Топ-N по біотопах, не класах:** unmapped-класи (забудова/рілля) пропускаються
  → стійкість до шуму. Логіка винесена в чисту `select_biotopes_from_histogram`.
- **Фон:** `threading.Thread` + статус у `calculation_log`
  (`source_name='biotope_autoassign'`) — копія патерну аналітики; точка заміни на Celery.
- **Graceful degradation (3 рівні):** нема пакета `ee` / нема ключа →
  `gee_landcover_available()` false → секція вимкнена, роут 503; помилка прогону →
  статус `failed`. Сайт не падає.

### Дефолти (засіяно на ct_db)
По `name_ua`, консервативно: 10 (Tree)→Мішаний ліс, 30 (Grass)→Лука,
80 (Water)→Стави, 90 (Wetland)→Торфовище. WorldCover не розрізняє
хвойний/листяний/мішаний ліс → клас 10 узагальнено в «Мішаний ліс». «Берег річки»
та підтипи лісу лишені на ручне уточнення в UI.

### Верифікація
- 14 нових unit/route-тестів (топ-N, dedupe, шумостійкість, gating, 503, 400) — pass.
- Повний набір тестів — pass (exit 0).
- Наскрізь на реальній ct_db: `gee_landcover_available()=True`; гістограми
  коректні (Карпатський НПП → Tree cover домінує); топ-N loc 1082 → [Мішаний ліс,
  Лука]; `unnest`+`ON CONFLICT` insert rowcount=2 (перевірено в транзакції з rollback).
- Таблиця створена + 4 дефолти засіяно (`scripts/init_biotope_autoassign.py`).

### Доповнення (2026-07-15, того ж дня)
За запитом додав до `ct_db.biotopes` загальні категорії під класи WorldCover,
яких бракувало (ids 8–14): Ліс, Чагарники, Рілля, Забудова, Оголений ґрунт,
Водойми, Водно-болотне угіддя. Мапінг переведено на загальні (10→Ліс, 20→Чагарники,
30→Лука, 40→Рілля, 50→Забудова, 60→Оголений ґрунт, 80→Водойми, 90→Водно-болотне).
Специфічні (підтипи лісу, Стави, Торфовище, Берег річки) лишились для ручного
тегування. Код синхронізовано: `DEFAULT_LANDCOVER_BIOTOPES` + оновлений
`DEFAULT_SEED_BY_NAME_UA` у `biotope_autoassign.py`; init-скрипт тепер створює ці
біотопи (ідемпотентно, ON CONFLICT (name_ua)) перед сідом мапінгу. +2 тести (16 разом).

### Доповнення 2 (2026-07-15) — прибрано редактор мапінгу з UI
За запитом: відповідність клас→біотоп задається лише в БД (`biotope_landcover_map`),
не в адмінці. Прибрано сітку-редактор із `admin.html`, її JS, роут
`POST /admin/biotopes/mapping` і збір `biotope_list`/`biotope_landcover_rows` у
`admin_panel`. В адмінці лишились кнопка + поля радіус/top_n + polling. Тест на
видалений роут прибрано (15 тестів). `set_biotope_mapping`/`get_biotope_mapping`
лишились у модулі як програмні хелпери для керування мапінгом з БД/шелу.

### Стан / наступний крок
- Код готовий, задокументований у CLAUDE.md. Масовий прогін по 894 локаціях **не
  запускався** — це робить адмін кнопкою (можливо, після уточнення мапінгу
  лісів/берега в UI).
- Деплой: коміт shared-ct → оновити посилання в biomon і myproject → на проді
  запустити `init_biotope_autoassign` (створить таблицю + засіє).
- Далі (опційно): Celery замість threading; уточнити відповідності лісів вручну.
