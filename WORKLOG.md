# WORKLOG — biomon

> Note: entries from 2026-08-14 on are written in English per the global
> documentation-language rule; earlier entries stay in Ukrainian as written.

## 2026-08-19 — Self-service registration for verifiers

### Request
Let people register themselves at biomon.app instead of being created by hand:
confirm the address by email (anti-bot), tick what they want to verify (photos /
sounds / both). Registration automatic; verification rights approved by an admin
(managers later). Approval must apply to public locations immediately; other
territories stay a manual grant.

### Design — two independent gates
* **Can this account log in?** `user.is_active`, flipped by the emailed
  confirmation link. `is_active` deliberately overrides `UserMixin.is_active`, so
  Flask-Login refuses the login itself, and `load_user()` now drops the session of
  an account disabled mid-session (Flask-Login only checks at login time).
* **What may it do?** One `verification_requests` row per (user, module), decided
  by an admin. A table rather than flags on the user: "photos + sounds" is two
  independently decidable requests, and each decision keeps who/when/why.

Approval adds `ct_verifier` / `pam_verifier` and **no institution** — which is
what makes the existing access model resolve to public locations only. That
turned out to be the load-bearing part; see the security section below.

### Files
* `app/models/__init__.py` — `User.is_active` / `email_confirmed_at` /
  `self_registered` / `locale`, `VerificationRequest`, guarded `load_user`.
* `migrations/versions/d41a7c9e5b02_add_self_registration.py` — new columns,
  UNIQUE index on `user.email`, the new table.
* `app/utils/registration.py` — account creation, confirmation, purge of stale
  signups, `get_or_create_role`.
* `app/utils/tokens.py` — signed, salted, expiring confirmation tokens (no token
  is stored in the DB; the payload is the address, so a token cannot be replayed
  after an email change).
* `app/utils/emails.py` — confirmation / decision / admin-notification mail, in
  the user's `locale`, delivered in a background thread (Flask-Mail 0.10 has no
  SMTP timeout — sending inline could hold a gunicorn worker).
* `app/utils/forms.py` — `RegistrationForm` (honeypot + reCAPTCHA + consent +
  "at least one module"), `ResendConfirmationForm`; password rules deduplicated
  into `_PASSWORD_VALIDATORS`.
* `app/routes/main.py` — `/register`, `/confirm/<token>`, `/resend-confirmation`,
  login gate, profile context.
* `app/admin/{routes,services}.py` — `VerificationRequestService` + the queue and
  decide endpoints (admin-only; the manager step is documented in
  `can_decide()`).
* Templates: `register.html`, `resend_confirmation.html`,
  `admin/admin_verification_requests.html`; login/nav/profile/admin-home updates.
* `app/commands.py` — `flask purge-unconfirmed [--days N] [--dry-run]`.
* `app/seo.py` — `/register` is indexable on purpose (it is a public call for
  volunteers, and QR posters will point at it); `/*/confirm/` and
  `/*/resend-confirmation` are disallowed.

### Security consequences (the real work)
Opening registration changes what `@login_required` means: it used to imply a
hand-created account and now means "anybody who confirmed an address". Audit of
every login-only endpoint found four real holes, fixed in the submodules:

* `shared-ct` `get_location_details` — returned name/coordinates/description for
  ANY location id. Now enforces visibility (404, not 403, so restricted locations
  stay undiscoverable). New shared helper `utils.can_access_location`.
* `shared-ct` `submit_identification` — accepted a client-supplied
  `observation_id` with no scope check: a verifier could write identifications
  for series they may not even view. Now guarded by `_can_access_observation`,
  which also replaced an inline copy of the same rule in the queue endpoint.
* `shared-ct` `api/next-observation-for-identification` — login-only; now
  `role_required('ct_verifier')`.
* `shared-ct` `api/stats/daily-activity/download` — with `scope_type=global` and
  no institutions it aggregated over EVERY location. Now public locations only
  for such accounts (users with institutions keep their previous numbers).
* `shared-pam` `api/verification/segments` — login-only and completely unscoped:
  it listed every segment's filename, location name and date. Now
  `role_required('pam_verifier')` plus the access baseline applied to all four
  queries in the endpoint (rows, total, per-status counts, average confidence),
  so the counters cannot advertise segments the user cannot open.
* `shared-pam` `api/verification/stats`, `api/top-verifiers`,
  `api/get-taxonomic-filters` — gated to `pam_verifier`.

**Deliberate semantic change:** `_segment_access_sql` used to return `FALSE` for
a user with no institutions. It now returns the public-locations branch
(`locations.visibility_level = 0`), OR-ed with the institution branch when there
are institutions. Without this an approved self-registered verifier would see an
empty queue — the public pool is exactly what their approval grants. This
supersedes the "fail closed to nothing" rule pinned in
`tests/test_pam_verification_unknown.py`, whose test and docstring were updated
rather than deleted. Checked against production pam_db (read-only): 20 903 of
29 724 segments are pending on public locations, so a new verifier has work; 1 256
segments remain outside the public pool.

### Migration notes
* The chain up to `b7d2e1a9c4f0` creates `user` WITHOUT `email` / `phone` /
  `created_at` / `created_by_id` — production acquired them outside Alembic, so
  `flask db upgrade` from scratch produced a schema that did not match the model.
  This migration backfills the two columns it needs (`email`, `created_at`) when
  absent; the rest of that historical gap is still open.
* `user.email` becomes UNIQUE. The upgrade aborts with the offending addresses
  listed if duplicates exist, rather than mangling data. Production checked
  (read-only): 47 users, 24 with an address, no duplicates — it will apply cleanly.
* Verified upgrade → downgrade → upgrade on a throwaway SQLite DB, including that
  the unique index rejects a duplicate and still allows several NULLs.

### Tests
* `tests/test_registration.py` — 46 cases: the flow end to end, both gates,
  idempotent confirmation, tampered / expired / wrong-salt tokens, confirmation
  for a deleted account, honeypot, per-IP rate limit, duplicate username/email
  (case-insensitive), enumeration-safe resend, admin approve/reject/re-decide,
  role gates on the queue, profile status, purge, `get_or_create_role`.
* `tests/test_public_scope_access.py` — 17 cases pinning "no institutions ⇒
  public only" for `can_access_location`, location details, identification
  writes, the daily-activity CSV, and the PAM access baseline + segment listing.
* `tests/conftest.py` — the CT in-memory fixture now creates `location_biotopes`
  (missing table made location-detail tests fail for the wrong reason).
* `tests/test_ct_identification_comment.py` — now builds its photo on a PUBLIC
  location: `Location.visibility_level` defaults to 1, so those four tests were
  relying on the missing scope check.
* Full suite: **1512 passed, 36 skipped** (63 new cases on top of the 1449 baseline).

### Not done / next steps
* Managers cannot decide requests yet (`assignable_by` is ready; the open
  question is what "their" applicant means when no institution is attached).
* A privacy-policy page and account deletion on request — we now collect personal
  data from the general public.
* SPF/DKIM/DMARC for biomon.app should be verified before announcing the feature,
  or confirmation mail will land in spam.
* Deploy needs `bash ~/update_all.sh` (interactive sudo) plus `flask db upgrade`
  and a cron entry for `flask purge-unconfirmed`.

## 2026-08-19 — PAM top-5 species on the profile page

### Request
`/en/profile` showed a top-species list for camera traps only. The PAM block
listed verifications / confirmations / unique species but no top species.

### Change
- `app/pam/utils.py::get_user_pam_stats` (submodule `shared-pam`) now takes
  `lang='uk'` and returns `top_species` — top-5 `[{name, count}]`. Species names
  come from `species.common_name_uk` / `common_name_en` with a
  `scientific_name` fallback. NOTE the column naming differs from ct_db, which
  uses `common_name_ua`.
- Counting rule: only CONFIRMED verifications (`verification_result = 1`), i.e.
  "species this person confirmed". A rejection is not a statement of interest in
  a taxon, unlike a camera-trap identification, where every row is positive —
  hence the two blocks carry different labels in the UI.
- `app/routes/main.py` passes `lang=lang_code` (the function previously had no
  language parameter at all, so PAM names could not be localised).
- `app/templates/profile.html` renders the PAM top-species list with the same
  markup as the CT one; new string "Топ види (за підтвердженими сегментами)" →
  "Top species (by confirmed segments)". pybabel rejected a fuzzy match to the
  CT string ("by series") — corrected manually before compiling.

The signature stays backward compatible (`lang` defaults to `uk`, existing keys
untouched) because `shared-pam` is also checked out by `myproject`.

### Tests
`tests/test_profile.py` — two new cases: both modules render their top-species
list, and `/en/profile` passes `lang='en'` into the PAM stats call. File: 10
passed. Full suite: 1449 passed, 36 skipped.

Read-only smoke test against the real pam_db confirmed both languages return
sensible data (e.g. Деркач / Corn Crake).

## 2026-08-14 — CT gallery opens on "all species" and loads immediately

### Symptom
`/uk/camera-traps/gallery` opened empty: the species select had no value,
the "Show photos" button was disabled, and the user had to pick a species
(or the "-- Всі види --" entry) and click before any photo appeared.

### Fix
- `app/camera_traps/templates/gallery.html` — the "all species" option
  (`id == 0`) is now rendered with `selected`, and the page calls
  `loadGalleryPhotos(speciesSelect.val())` once on init instead of waiting
  for a click. The button state is synced at init so it stays enabled.
- No backend change: `get_gallery_photos` already treats `species_id == 0`
  as "all species" (`routes.py`), and `gallery()` already puts that entry
  first in `available_species`.

Clearing the select (select2 `allowClear`) still disables the button, so the
manual path is unchanged.

### Tests
`tests/test_ct_pages_access.py::TestGalleryAccess` gained two cases: the
`value="0" selected` option is present in the rendered HTML, and the auto-load
call is emitted. Full file: 59 passed.

Committed in submodule `shared-ct` as `d3b8515`.

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


## 2026-08-17 — SEO: crawl control for CT/PAM/SDM + canonical host

*(Note: this WORKLOG is historically in Ukrainian; per the current global
convention new entries are written in English.)*

### Context
Google Search Console coverage export for biomon.app (2026-08-17) showed 21
pages indexed against 43 not indexed, with the not-indexed count climbing
steadily since the property was added (13 on 30.06 → 43 on 14.08). The reason
breakdown was mild (30 "excluded by noindex" — all `/login?next=…` variants,
5 "Google chose a different canonical", 4 redirects), but the *shape* matched
the sibling property yurastrus.dev before its June rework, which had reached
102 000 "crawled – currently not indexed".

Full audit (both properties): `C:/Temp/seo-coverage-audit-2026-08-17.md`.

### Findings acted on

**F6 — no crawl control at all.** biomon serves the same shared camera_traps /
pam submodules as yurastrus.dev, with the same query-parameterised dashboards,
but none of the countermeasures. Verified live: `/uk/camera-traps/dashboard?…`,
`/uk/camera-traps/gallery?page=2`, `/uk/pam/trends` and even
`/uk/camera-traps/api/stats/top-species` all returned `200` + `index, follow`.
That is an effectively infinite URL space offered to Googlebot.

**F1 — `www.` served a full second copy.** `https://www.biomon.app/uk/`
returned 200 with `canonical="https://www.biomon.app/uk/"` — every page existed
twice, each canonicalising to itself. This is what produced "Google chose a
different canonical", and it doubled the crawl budget.

### Changes

- `app/seo.py`: added `INDEXABLE_ENDPOINTS`, derived from `PUBLIC_ENDPOINTS`, so
  the sitemap and the noindex allowlist cannot drift apart. (On the sibling site
  those two lists *had* drifted and silently hid a whole new section.)
- `app/seo.py::robots_txt`: `Disallow` for the JSON API trees, the media/static
  trees (`/thumbnails/`, `/photos/raw/`, `/ct-static/`, `/*/pam-static/`,
  `/*/audio/`), the auth-walled trees that 302 to sign-in, and `/*?`.
- `app/__init__.py`: new `_register_seo_hooks()`, holding
  - `before_request` → 301 `www.*` to the apex host;
  - `after_request` → `X-Robots-Tag: noindex, follow` for every
    camera_traps / pam / sdm response except the clean landing hubs.
- `tests/test_seo.py`: +16 tests (landings indexable, dashboards noindex,
  own pages untouched, robots rules present, stage-2 rules *absent*, www→apex).

### Decision: why a header and not a `<meta>` tag
`app/camera_traps` and `app/pam` are public git submodules shared with
myproject/yurastrus.dev. Editing a template there would leak into the other site
and require pushing to a public repo we do not own the release cadence of.
`X-Robots-Tag` is equivalent to meta robots for Google, lives entirely in this
repo, and additionally covers JSON endpoints where a meta tag does nothing.
`git submodule status` is clean and stays clean.

### Decision: robots stage 2 deliberately NOT enabled
The clean dashboard URLs (`/uk/camera-traps/dashboard`, `/uk/pam/pam_overview`,
…) are still *in* the index. A `Disallow` would stop Googlebot from reading the
new `noindex` header and freeze them there. They stay crawlable until Search
Console shows them dropping; the stage-2 lines sit commented in `robots_txt`
with a test asserting they remain off. `/*?` is safe to block now because the
query variants were never indexed.

### State / next step
- Tests: 1440 passed, 3 failed — the three failures are pre-existing and
  order-dependent (`test_pam_verification_priority`), reproduced identically on
  a clean checkout before these changes.
- Not done here: the nginx-level `www` → apex 301. The Flask redirect above
  covers it, but a server-level `return 301` is cheaper and should replace it.
- After deploy: resubmit `sitemap.xml` in Search Console, then re-export
  coverage in ~3 weeks and decide on stage 2.
- Open question for the owner (audit F7): the sitemap declares only the five
  landing hubs while 21 pages are indexed. If any biomon page is *content*
  rather than tooling, it belongs in `PUBLIC_ENDPOINTS`.

## 2026-08-17 (later) — fixed the three long-standing PAM test failures

### What was actually broken
`tests/test_pam_verification_priority.py` had been failing on all three tests
since the bilingual-location change to `api_next_verification_segment`. The
route reads its row positionally; the SELECT list grew from 10 to 12 columns
(`l.location_name`, `l.location_name_en` from the locations registry) but the
test's `FAKE_ROW` mock stayed at 10:

```
IndexError: tuple index out of range
  app/pam/routes.py:1613   loc_name_uk = result[10]
```

So the route was fine and the mock was stale — the tests reported a 500 with no
hint at the cause.

### Changes
- `FAKE_ROW` extended to the full 12 columns, annotated index-by-index against
  the SELECT list.
- `test_fake_row_arity_matches_route_positional_reads` — parses the route source
  (read-only; `app/pam` is a shared public submodule) for `result[N]` reads and
  asserts the mock is long enough. The next drift fails with a message that says
  what happened, instead of a mystery IndexError.
- `test_next_segment_prefers_registry_location_over_filename` — exercises the two
  columns that were missing, so they are covered rather than merely present.

### Also: the flaky fourth test, and why it was a real defect
`test_next_segment_no_institution_filter_when_absent` asserted
`'location_institutions' not in sql`. Two different things join that table:

- the **optional** UI filter (`?institution_ids=…`, alias `li`, param
  `:institution_ids`) — which this test legitimately wants absent;
- the **mandatory** ACCESS baseline (`_segment_access_sql`, alias `li_acc`,
  param `:access_inst_ids`) — which restricts a verifier to their own
  institutions and must **always** be there.

The assertion conflated them, so it only held while that particular verifier
happened to have no institution link (in which case the baseline degrades to
`FALSE`). Any state that gave the user an institution flipped the test — that is
the flakiness, and the assertion was also forbidding the security-relevant
clause. Retargeted it to the optional filter's own markers, and added the two
tests that pin the invariant instead:

- verifier **with** an institution → `li_acc.institution_id` present and
  `:access_inst_ids` bound to exactly that user's institutions, no UI filter;
- verifier **without** one → `FALSE`, i.e. fails closed and matches nothing.

No submodule files touched; `git submodule status` clean.

### State
Full suite: **1447 passed, 36 skipped, 0 failed**. The suite is green for the
first time in this checkout's history.
