# WORKLOG — biomon

> Note: entries from 2026-08-14 on are written in English per the global
> documentation-language rule; earlier entries stay in Ukrainian as written.

## 2026-08-20 — Opt-out for the weekly identification reminder

### Request
`flask send-id-reminders` (cron, Mondays 09:00) mails every `ct_verifier` with an
email address and 10+ pending series. There was no way to stop receiving it short
of an admin deleting the address. Wanted: a checkbox in the personal profile,
on by default, editable by the account owner and by an admin only; the mail
itself should explain where the checkbox is; and the design should anticipate a
future PAM notification that does not exist yet.

### Mechanism as found
`app/commands.py` → `flask send-id-reminders` → `app.camera_traps.notifications.
send_identification_reminders()`. It walks the `ct_verifier` role, counts pending
series per user with the same logic as `/api/identification-stats`, and sends to
anyone at 10 or more. No preference storage of any kind; cron is on the server
(`0 9 * * 1`, logged to `/var/log/biomon_reminders.log`), not in this repo.

### Design
**A registry, not a bare column check.** `app/utils/notification_prefs.py` holds
`NOTIFICATION_PREFS` — key, `User` column, label, hint — and the profile page,
the admin user form and both POST handlers iterate it. Adding the PAM digest is
then a migration plus one tuple, with no template or route edits. The module
docstring spells out the three steps.

**Only CT is listed.** A checkbox that unsubscribes from something nobody sends
is worse than no checkbox, so the PAM entry is a comment, not a disabled row.
The column for it lands with the digest.

**Opt-OUT, not opt-in.** `notify_ct_pending` is `default=True` /
`server_default=true` (migration `e5c31b8a7d94`), so the migration cannot
silently unsubscribe the existing verifiers.

**Absence means off.** An unchecked HTML checkbox sends nothing, so
`apply_form()` reads absence as False. That is only safe where the whole section
is rendered — hence the manager case below.

### Permissions
The owner edits their own on `/uk/profile`. `/uk/admin/users/edit/<id>` is open
to admin **and** manager, but the request was admin-only, so the block is gated
on `current_user.has_role('admin')` in both the route and the template. A
manager's POST carries no `notify_*` fields, and had the route applied the form
unconditionally that absence would have unsubscribed the user on every unrelated
edit — so the route skips `apply_form()` entirely for them. There is a test for
exactly that.

### The email
No one-click unsubscribe link: that needs a signed token and a public route, and
the letter goes to a handful of verifiers. Instead the body now spells out the
three clicks (log in → Мій профіль → «Сповіщення» → зняти галочку → зберегти),
links `/uk/profile`, and says the opt-out does not touch access or rights. The
letter stays Ukrainian-only, as it already was — localising it by `user.locale`
is a separate job and was not part of this request.

Opted-out users are filtered out *before* the pending-series count, so an
unsubscribe holds no matter how large the backlog is; the count is the expensive
part anyway.

### shared-ct coupling
`notifications.py` lives in the shared-ct submodule, which other host apps may
use. The import of the registry is soft — `try/except ImportError` with a
`getattr(user, 'notify_<key>', True)` fallback — so a host without the registry
keeps working and treats "no preference" as subscribed.

### Verification
`tests/test_notification_prefs.py` (11 tests): default is on; the registry keys
map to real columns; the profile round-trips both directions; an unrelated
username change does not unsubscribe; admin sees and can clear the box; manager
sees nothing and their POST is a no-op; the sender skips opted-out users without
even counting their series; a subscribed user still gets the mail; the body names
the profile URL, the section and the checkbox. Full suite: 1538 passed, 36
skipped. Migration applied to a copy of `app.db` — column lands as
`BOOLEAN DEFAULT 1 NOT NULL`.

### Migration applied to production ahead of the code (2026-08-20)
`DATABASE_URL` in `.env` reaches the production Postgres through the SSH tunnel
on `localhost:5433` — worth knowing before running anything from a workstation.
Production was at `d41a7c9e5b02`, exactly this migration's `down_revision`, so
`flask db upgrade` ran a single step. Offline preview (`--sql`) confirmed one
statement, `ALTER TABLE "user" ADD COLUMN notify_ct_pending BOOLEAN DEFAULT true
NOT NULL` — additive, no data rewrite, and the deployed code never references the
column, so the schema stayed compatible with the running build. After: 67 users,
all 67 subscribed, none opted out. Log in `logs/db_upgrade_notification_prefs_20260820.log`.

Generating that preview exposed a defect in the migration: the
already-applied guard called `sa.inspect(op.get_bind())`, which has no live
connection in offline mode and raised `NoInspectionAvailable`. Guarded with
`context.is_offline_mode()` — offline runs now just emit the ADD COLUMN.

Nine new UI strings extracted and translated into English; `pybabel update`
fuzzy-matched three of them onto unrelated entries ("Сповіщення" → "Message",
"Зберегти налаштування сповіщень" → "Role settings"), which were corrected by
hand. `.mo` files recompiled — `update.sh` does not run `pybabel compile`, so
they must be committed.

## 2026-08-20 — AI prediction badge on /camera-traps/identify

### Request
Three tweaks to the AI badge above the species list: drop the robot pictogram,
show the species in the interface language instead of the raw model label
(`bear`) and with no Latin name, and put confidence and individual count on one
line separated by a semicolon.

### Changes
- `ai_runner.get_observation_ai_prediction()` now takes `lang_code` and, when the
  prediction is mapped to a `species` row, returns `common_name_ua` /
  `common_name_en` (by locale) as `species_label`. Scientific name is used only
  as a last resort if both common names are empty; the raw model label stays the
  fallback for predictions with no `prediction_species_id`. Deliberately unlike
  `get_species_with_ai_predictions()`, which appends `(Scientific name)` — the
  badge was asked to carry no Latin.
- Both callers in `routes.py` (`/identify` page load and the next-observation
  JSON) pass `lang_code=g.lang_code`.
- `identification.html`: removed the `.ai-pred-icon` span; the badge text is now
  two block spans — `.ai-pred-species` ("AI пропонує: <name>") and
  `.ai-pred-meta` ("впевненість: 99%; особин: 1"). Still built with jQuery
  `.text()`, so the species name cannot inject markup.
- `camera_traps.css`: dropped the now-unused `.ai-pred-icon` rule, added the
  block display for the two spans, `flex: 1 1 auto` on `.ai-pred-text` so it
  fills the badge now that nothing sits beside it.

No new translatable strings — "AI пропонує" / "впевненість" / "особин" already
exist in the uk and en catalogues, so no `.po` regeneration was needed.

## 2026-08-19 — Ecoregion editable in the institutions admin

### Request
`/uk/admin/institutions` could rename and add institutions but not assign them a
natural region, so a newly added one (Рівненський ПЗ) had `ecoregion_uk = NULL`
while the camera-trap and PAM "Institution / Ecoregion" filters read exactly that
column. Wanted: a dropdown with the ability to add a value that is not in the list.

### Data fix
Set `ecoregion_uk = 'Полісся'`, `ecoregion_en = 'Polissia'` for `code = 'RVSNR'`
(id 29) directly in production. Still without a region afterwards: id 28
"Північні торфовища проект" — left alone, it was not part of the request and its
region is a judgement call.

### What the table actually holds
`institutions` has exactly six columns — id, name_uk, name_en, code,
ecoregion_uk, ecoregion_en — matching the model. So the ecoregion pair was the
only thing missing from the form; nothing else hangs off an institution (the
user link lives in `user_institutions`, with `can_export`, edited on the user
form).

### Design
There is no ecoregions table, so **the vocabulary is the set of values already in
use**: `InstitutionService.get_ecoregions()` returns the distinct (uk, en) pairs,
the `<select>` offers them plus "+ Додати новий регіон…", and a new value becomes
part of the vocabulary simply by being saved once.

The pair is kept together on purpose: picking "Полісся" also stores "Polissia",
resolved from the DB rather than retyped. Two independent free-text fields would
let the uk and en names drift apart, and the CT/PAM filters group by
`ecoregion_uk` while displaying `ecoregion_en` — a mismatch would show up as
duplicate or unlabelled groups. The free-text pair is only reachable through the
"new region" option, and `resolve_ecoregion()` rejects both an empty Ukrainian
name and a selected value that is not a known region (crafted POST).

### Files
* `app/admin/services.py` — `ECOREGION_NEW`, `get_ecoregions()`,
  `resolve_ecoregion()`; `create()` / `update()` take the ecoregion pair.
* `app/admin/forms.py` — optional `ecoregion_uk` / `ecoregion_en` (length only);
  the `<select>` stays out of WTForms because its options come from the DB, same
  pattern as the institution/role checkbox lists.
* `app/admin/routes.py` — resolves the choice, re-renders with the error instead
  of saving when it is invalid.
* `admin_institution_form.html` — dropdown + a collapsed free-text block toggled
  by a five-line inline script.
* `admin_institutions_list.html` — new column; institutions without a region are
  flagged in red so this cannot silently happen again.

### Tests
`tests/test_admin_institution_ecoregion.py` — 15 cases: the vocabulary (including
the same region entered once with and once without an English name), all four
resolve paths, the rejected ones, preselection on the edit page, saving an
existing / brand-new region, clearing it, "nothing is saved on a rejected submit",
and that renaming an institution keeps its region.

Full suite: 1527 passed, 36 skipped. No migration needed — the columns already
existed.

One pre-existing flake surfaced during this run:
`test_registration.py::test_registering_while_logged_in_redirects_to_profile`
failed once in a full-suite run and passed everywhere in isolation. Cause is the
Flask-Login identity cache on `g`: an earlier test that leaves an app context
pushed lets an AnonymousUser cached by an anonymous request survive into the next
test, so the authenticated GET saw an anonymous user. Fixed by dropping the cache
in that test, like the others that switch identity.

## 2026-08-19 — Incident: 500 after deploying self-service registration

### Symptom
Every page returned "Internal Server Error" right after `update_all.sh`.

### Cause
The production main database had **never been under Alembic**: no
`alembic_version` table at all — the schema had been built with `create_all()`
plus ad-hoc changes. So:

* `update.sh` never ran `flask db upgrade` (only the SDM migrations), and
* even if it had, the upgrade would have tried to replay the whole chain from the
  root revision and failed on already-existing tables.

The deployed code therefore expected `user.is_active` / `email_confirmed_at` /
`self_registered` / `locale` and `verification_requests`, none of which existed →
every request that loaded a user raised.

### Fix applied on the server
```bash
venv/bin/flask db stamp b7d2e1a9c4f0   # declare the existing schema as the previous head
venv/bin/flask db upgrade              # applies only d41a7c9e5b02
```
No gunicorn restart was needed: the code was already the new one, and the columns
appeared underneath it. Verified afterwards: the four columns and the new table
exist, `uq_user_email` is in place, 49 users are active, and `/uk/`, `/uk/pam`,
`/uk/camera-traps/`, `/uk/login`, `/uk/register` (uk + en),
`/uk/resend-confirmation`, `/robots.txt`, `/sitemap.xml` all return 200.

Also smoke-tested the registration POST against production with a valid CSRF
token but no captcha response: it re-renders the form with the captcha error and
creates nothing (`user WHERE username LIKE 'smoketest%'` → 0,
`verification_requests` → 0). reCAPTCHA renders with a real site key.

### Note on the stamp
`flask db stamp b7d2e1a9c4f0` also marks revision `1e9c5f810461` (the chain root:
`issue` / `tag` / `journal_article*` tables) as applied. Those tables do not exist
in production and have no models in this codebase any more — legacy from an
earlier project. Stamping is the intended behaviour here: we do not want them
created. Production tables are exactly: contact_submissions, institutions, role,
site_text_content, user, user_institutions, user_roles, verification_requests.

### Prevention
`update.sh` now runs `venv/bin/flask db upgrade` (step 4) **before** the restart,
without a `|| echo` fallback: with `set -e` a failed migration aborts the deploy
instead of restarting gunicorn against a mismatched schema.

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


## 2026-08-28 — Admin panel made usable on a phone

### Problem
Reported from the field: the admin pages are unusable on a phone — "неможливо
нічого редагувати". The site itself has been responsive for a while (viewport
meta, collapsible header nav, dashboard grids collapsing at 992px), but nothing
below that had ever been applied to `/admin`. Those pages are five data tables
plus three dense forms, and at ~375px:

- a `<table>` either overflowed the viewport or crushed every column to one word
  per line. The overflow was not even scrollable: `.container` sets
  `overflow: hidden`, so anything past the right edge was clipped and gone.
- `main .container:not(.maplistcontainer)` is a **centred flex column**, so the
  admin blocks shrank to their content width — 234px of a 356px viewport.
- the action buttons were 12px links about 24px tall, well under a usable tap
  target, and the row of filter controls kept its desktop `flex` basis.

### Approach
Same shape as the camera-traps module (`app/camera_traps/static/css/camera_traps.css`,
section 21): one documented block of overrides at the bottom of the stylesheet
rather than inline styles scattered across templates. Where a template carried
an inline `style="display:flex; …"` that the media query would have to fight,
the inline blob was replaced by a class hook (`.admin-toolbar`,
`.admin-filter-bar`, `.admin-form-actions`, `.admin-cell-actions`,
`.inst-access-scroll`) defined next to the other admin rules.

Below 768px each table becomes a stack of cards: one card per row, one labelled
line per cell. The label is `content: attr(data-label)` on `td::before`, so every
`<td>` in the five list templates gained a `data-label`. `<thead>` is visually
hidden (clip rect, not `display:none`) so it stays in the accessibility tree.
Cells without a `data-label` — the "nothing found" `colspan` rows — render plain.

### Changes
- `app/static/css/style.css`
  - `.button-small` / `.button-danger` were only defined as `.admin-table td a.…`,
    so every `<button class="button-small">` in an action cell and every status
    filter pill outside a table (verification requests, contact submissions) fell
    back to the browser default. Added element-agnostic definitions; the in-table
    anchor rules stay as the more specific override.
  - New admin layout helpers (the class hooks listed above) + `.admin-filter-label`
    / `.admin-filter-input` lifted out of the users-list template.
  - New `@media (max-width: 768px)` admin section: tables → labelled cards,
    action cells as a wrapping flex row of ≥42px targets, filter bar stacked,
    form footers `column-reverse` (primary action first, under the thumb),
    inputs at 16px so iOS Safari does not zoom on focus, and the admin blocks
    `align-self: stretch` to undo the centred-flex shrink.
- The nine `app/templates/admin/*.html`: `data-label` on every data cell, class
  hooks in place of the inline flex blobs, `.admin-form-card` on the three form
  containers.
- `app/templates/base.html`: stylesheet cache-buster `v=2` → `v=3`.

### Decisions
- **Cards, not horizontal scroll.** The camera-traps precedent for a wide table
  is `.table-scroll` (`overflow-x: auto`). That suits a read-only analytics
  table, but the complaint here is about *editing*: side-scrolling to reach the
  action column on every row is exactly what made it unusable. Cards keep each
  record's actions on screen.
- **Scoped, not global.** `.dashboard-section` is shared with the CT and PAM
  dashboards, so the mobile padding override is keyed to `.admin-form-card`
  instead. Likewise the `align-self: stretch` fix lists the admin blocks by
  class rather than touching `main .container`, whose centring the rest of the
  site depends on.
- Two buttons inside one `<form>` (approve / reject on a verification request)
  are left stacked full-width rather than squeezed side by side — the extra row
  is cheap and mis-tapping "Відхилити" is not.

### Verification
Rendered all seven admin pages through the test client into static dumps and
measured them in a headless browser at 375×812 and at 1280px:

- 375px: no horizontal overflow on any page (`scrollWidth == innerWidth == 375`),
  blocks full-width (336px of 375), rows as cards with the right labels, action
  controls 149×43 / 307×42, inputs 42px tall at 16px.
- 1280px: `display` still `table-row` / `table-cell`, `thead` visible, labels
  suppressed, filter bar back in a row — desktop untouched.

Two bugs surfaced while measuring and were fixed: a direct-child `<button>` in an
action cell took `width: 100%` and so claimed a whole row (it is a flex item —
it must flex), and `.site-input` is redefined in a per-template `<style>` block
that comes after the stylesheet, so the mobile rule had to match on
element+class to outrank it.

Also fixed in passing: the empty state in `admin_institutions_list.html` spanned
five columns in a six-column table.

### State
Full suite: **1569 passed, 36 skipped**. Deployed 2026-08-28 and confirmed
working on a phone by Iurii.


## 2026-08-28 (later) — Telling the user when verification rights are granted

### What was actually missing
Reported as "no letter arrives after a verification request is approved". The
queue path already sends one and has since 2026-08-19:
`admin.decide_verification_request` → `send_decision_email()`. Exercised it
through the test client with the mail boundary patched, and the letter is
produced correctly (uk/en, approve/reject, with the admin's note).

The gap is the *other* way rights are granted. `admin.edit_user` — ticking
`ct_verifier` / `pam_verifier` in Користувачі → Редагувати — changed the roles
and sent nothing. That is how a queue applicant is often approved in practice,
and in that case:

- the person gets the rights but is never told, which is the reported symptom;
- their `verification_requests` row stays `pending` forever, so the queue keeps
  showing work that is already done — and approving it there later would send a
  second letter for the same grant.

Neither path had a single test, so a regression in either was invisible.

### Changes
- `send_rights_granted_email(user, modules)` in `app/utils/emails.py`. Kept
  separate from `send_decision_email` because the events differ: that one
  answers a request the person filed ("your request was approved"), this one
  announces rights an admin granted directly, which the recipient may never have
  asked for. One letter lists every module granted in the same save.
- `VerificationRequestService.resolve_pending_for_roles()` marks the matching
  pending requests approved against the same decider, so the queue stays truthful
  and no duplicate letter can follow.
- `admin.edit_user` diffs the role names around `UserService.update_user()`,
  resolves the pending rows before the commit, and emails after it.
- `tests/test_verification_decision_email.py` — 11 tests covering both paths:
  approve/reject wording, the admin's note, the English letter, no second letter
  on a re-decide or a re-save, unrelated roles sending nothing, both modules in
  one letter, and a user without an address neither crashing nor blocking the
  grant.

### Decisions
- **Two letters, not one reused.** Reusing "Ваш запит підтверджено" for someone
  who never filed a request reads as a reply to nothing.
- **Closing the pending row is part of this, not scope creep.** Without it the
  admin who granted by hand still sees the request in the queue and approving it
  there sends the applicant a second letter about rights they already have.
- **No opt-out preference.** These are transactional letters about the
  recipient's own access, not a digest; `NOTIFICATION_PREFS` stays for the
  weekly reminders.
- The email is sent after the commit, so a failed save cannot announce a grant
  that did not happen. Delivery is already fire-and-forget in a thread and never
  raises into the request.

### Verification
Mutation-checked the new tests: stubbing out the `send_rights_granted_email`
call makes exactly the three letter-asserting manual-path tests fail, so they
are not passing vacuously.

Full suite run twice: **1580 passed, 36 skipped**.

⚠️ The first of those two runs also had one failure,
`tests/test_pam_verification_unknown.py::test_submit_unknown_no_discard_if_meaningful_votes_exist`,
which passed on its own and passed on the second full run. Order-dependent flake
in PAM vote handling, unrelated to anything here (this change touches only the
admin blueprint) — but it is real and worth chasing separately, since the suite
is otherwise expected to be reliably green.

### State
Committed and pushed. Deployed 2026-08-28 and confirmed working by Iurii.


## 2026-08-28 (later still) — chasing the flaky PAM unknown-vote test

`test_pam_verification_unknown.py::test_submit_unknown_no_discard_if_meaningful_votes_exist`
failed once during a full-suite run earlier today, then passed on its own and on
the next full run. **Not reproduced, not fixed.** What follows is the evidence,
so the next person does not repeat it.

### What was ruled out
- **Not intrinsic to the route or the mock.** Driving the exact request 400
  times inside one process — same fixtures, same mock connection — produced 400
  correct responses.
- **Not a pairwise interaction.** Ran every other test file followed by this
  single test (≈90 combinations). No file breaks it.
- **Not ordering or hash-seed dependent.** 9 full-suite runs: 5 on an idle
  machine, 4 under saturating CPU load (13 busy cores) with PYTHONHASHSEED
  1–4. All 1580 passed. Load was worth trying because the one failing run took
  6:39 while an idle run takes ~3:00 — the failure happened on a busy box.
- **Not the "role silently dropped" theory.** `make_user` triggers a SQLAlchemy
  autoflush warning ("Object of type <User> not in session, add operation along
  'Role.users' won't proceed"), which reads exactly like a dropped role — and a
  user without the admin role would 403 on this endpoint. Tested it directly:
  the roles do persist, before and after a reload. Red herring.
- **Not stray-thread contamination of the mock.** The assertion
  `not any('discarded' in s for s in sqls)` is a substring match over every SQL
  executed on the mock, so a leaked background thread calling the patched
  `get_pam_db_connection` could in principle poison it. But the only PAM SQL
  containing the word is the discard UPDATE inside this very route
  (`app/pam/routes.py:1776`), and no test exercises the one route that spawns a
  PAM thread.

### What changed
Nothing that pretends to fix the flake. Three things found on the way:

1. **The test now says what went wrong.** It went straight from the response to
   `body['discarded']`, so any non-200 (403 from the access check, 500 from the
   route's broad `except Exception`) failed with a bare `KeyError: 'discarded'`
   that named no cause — which is why the one occurrence taught us nothing. It
   now asserts `resp.status_code == 200` with the body in the message, as its
   three sibling tests already did. The assertions themselves are unchanged.
2. **`MAIL_SUPPRESS_SEND` / `MAIL_SERVER = None` in `TestingConfig`.** Real
   hazard: `config.py` `load_dotenv()`s the actual `.env`, so the suite ran with
   the production mail server configured and nothing suppressing delivery. Any
   unpatched path reaching `send_email()` would spawn its delivery thread and
   talk to that server, possibly mailing whatever address a fixture invented.
   Nothing exercises such a path today, but the `edit_user` change earlier
   today added one more way to reach it.
3. **`make_user` adds the user before appending roles**, silencing the
   misleading autoflush warning above (12+ per run).

### If it comes back
The status assertion will name the cause. A 403 means the admin role was not in
effect for that request; a 500 means the route raised and the traceback is in
the captured log.

### State
Full suite: **1580 passed, 36 skipped**. Note this leaves a known, unexplained
one-in-many flake in the suite — it is not an accepted baseline failure, just an
unresolved one.

## 2026-09-02 — Second-tier backup: per-institution CSV export of camera-trap data

### Why
The nightly `full_backup.sh` produces `.sql.gz` dumps of every database plus a
GeoServer archive. That is a restore path, not a readable copy: it helps only
someone who can rebuild a running biomon. Requested second layer — export the
camera-trap data as CSV, one file per institution, so the numbers survive in a
form a person can open, and each park effectively holds its own copy.

### What was built
- `app/backup/storage.py` — storage backends behind one four-method interface
  (`put`, `read_manifest`, `write_manifest`, `rotate`). `LocalStorage` writes a
  directory; `RcloneStorage` drives the rclone binary already installed for the
  dump sync. Adding Nextcloud later is one class plus one config line.
- `app/backup/ct_csv.py` — the exporter. Rows come from
  `app.camera_traps.data_export.get_ct_occurrence_data`, the same function
  behind `/api/data-download`, so the backup columns cannot drift from the UI.
- `config.py` → `CT_CSV_BACKUP`; `app/commands.py` → `flask ct-csv-backup`;
  `deploy/ct_csv_backup.sh` — cron/full_backup.sh wrapper.
- `tests/test_ct_csv_backup.py` — 29 tests.

### Decisions and why
**Reuse the query, not the endpoint.** Calling the HTTP route from cron would
need a session, a user with export rights, and CSRF handling, all to reach a
function that is importable. The exporter calls `get_ct_occurrence_data`
directly and passes `institution_ids=[id]`, which is exactly what the route's
`_get_export_institution_ids()` resolves to for a single-institution download.

**Change detection by content hash, not by a database timestamp.** The
alternative was `max(updated_at)` over the source tables, which is cheaper but
wrong in both directions: ct_db has no reliable update timestamp on every table
the export touches (identifications, ai_predictions, deployments all feed the
result), and a change that does not alter the exported columns would still
force a write. Hashing the rendered CSV asks the only question that matters —
"is the output different?" Cost is one query per institution per night, which
the existing dump job dwarfs.

**Rotation counts versions; it does not reuse `RETENTION_DAYS`.** The request
was to reuse the dump script's retention variable. That variable is age-based
(`find -mtime +$RETENTION_DAYS`) and currently `0`, i.e. "delete anything older
than a day". Combined with the change check that is actively harmful: a park
with no new data would not get a fresh CSV, and the day-old one would be
deleted, leaving nothing. So `KEEP_VERSIONS` (default 2) counts files instead.
`keep=0` is clamped to 1 — "no backup at all" is not a state this module will
enter on a config typo.

**Google Drive comes free.** `phototraps_data/` is created inside
`$BACKUP_ROOT`, which `full_backup.sh` already mirrors with
`rclone sync … gdrive_backup:backups/my_server`. No second upload, no extra
credentials, and the local rotation propagates to Drive because `sync` mirrors
deletions. The `rclone` backend stays in the code for a genuinely different
destination and is enabled only when `CT_CSV_BACKUP_REMOTE` is set.

**Backup filters, not page defaults.** Answered by the user: `human_ai` +
`filter_type='all'` + whole history. So the CSV carries consensus rows, every
competing identification of an unresolved series, AI-only series, and non-animal
records. The export page defaults (consensus / animals only / current year) are
right for a person preparing an analysis and wrong for a backup.

**Per-backend manifests.** Each destination stores its own `manifest.json`, so a
remote that was unreachable yesterday catches up on the next run even though the
local copy is already current. A shared manifest would have marked the data
"unchanged" and left the remote permanently a version behind.

**Failure isolation.** A backend error is logged and the remaining backends
still write; an institution whose query raises is recorded in the report and the
remaining institutions still get their backup. The command exits 1 if anything
failed, but nothing already written is rolled back.

### Folder and file naming
`Institution.name_en` → ASCII slug for the directory (`Roztochya_Nature_Reserve`),
`Institution.code` → filename prefix (`RSNR_ct_occurrence_2026-09-02.csv`). Both
read from the database. An institution without an English name falls back to
`institution_<id>` rather than to the Ukrainian name, because the folder is
created on the server, in Google Drive and possibly on Nextcloud, and non-ASCII
directory names behave differently on each.

### Testing
`tests/test_ct_csv_backup.py` — 29 tests: slug edge cases, CSV rendering,
manifest round-trip and corrupt-manifest tolerance, rotation (including that it
leaves unrelated files alone and never empties a folder), the rclone command
contract, the backup-specific filters, unchanged-vs-changed behaviour, dry run,
failing-backend isolation, per-backend manifests, and per-institution isolation.
`TestingConfig` now ships an empty `CT_CSV_BACKUP` so a test that forgets to
patch cannot write into the real server backup root.

Full suite after the change: **1609 passed, 36 skipped**.

### Not done — needs sudo, left to the user
Deployment. Two manual steps on the server:
1. `install -m 755 /var/www/biomon/deploy/ct_csv_backup.sh /usr/local/bin/`
2. Insert the `4b` block (text is in the header of `deploy/ct_csv_backup.sh`)
   into `/usr/local/bin/full_backup.sh` before section 5, the rclone sync.
Then verify with `/usr/local/bin/ct_csv_backup.sh --dry-run`.

### Worth watching
The volume is at 96% (6.7 GB free). The CSVs are small next to a 906 MB
`geodata` dump, and the change check keeps quiet parks from writing at all, but
this layer does add files to a disk that has little headroom.

## 2026-09-02 (later) — CSV backup: wrong filter caught on real data, deployed, verified

### What went wrong with the first version
It shipped with `filter_type='all'`. That was chosen deliberately — "a backup
should lose nothing" — and confirmed with the user before implementation. On
real data it was simply the wrong call.

`ct_db` carries 28 pseudo-species with negative ids: `empty`, `vehicle`,
`motobike`, `quadbike`, `Homo sapiens`, `not identifiable`, and so on. They are
machine labels for frames with no animal in them. For Roztochya alone, the
AI-only branch produced 2 023 animal rows against **9 189** non-animal ones.

Result: 22 349 rows instead of 6 090, and a query that ran **2 min 25 s** for a
single institution — inside the nightly pipeline, multiplied by 24 parks.

The cost was not only row count. `s.id > 0` prunes before the window functions
sort, so `AIPick` no longer drags all 760 205 AI predictions through a sort.
Dropping the pseudo-species took the run from 2 min 25 s to **23 s**.

The lesson is about how the question was asked, not about the code. The options
presented "maximum completeness" as recommended and mentioned empty frames in a
parenthesis, without a number next to it. Counting the pseudo-species first and
showing "9 189 junk rows per park" would have made the answer obvious.

### The fix
- `DEFAULT_FILTER_TYPE = 'species_only'`, `DEFAULT_EXPORT_MODE = 'human_ai'`.
- Both moved into `CT_CSV_BACKUP['EXPORT_MODE'/'FILTER_TYPE']` and env vars, so
  changing the policy no longer means editing code.
- `resolve_filters()` validates them. Not cosmetic: `get_ct_occurrence_data`
  silently coerces an unrecognised `export_mode` to `'consensus'`, so one typo
  in the config would quietly shrink every backup to consensus-only rows with
  nothing in the log to say so.
- The manifest now records which filters produced the file, so an old copy can
  be told apart from one made under different rules.

### Verification against a real download
The user downloaded the RSNR table by hand from the export page as a reference.

| | Manual download | `ct-csv-backup` |
|---|---|---|
| Rows | 6 090 | 6 090 |
| Localities | 28 | (RSNR owns 30 locations) |
| Size | 2 515 503 B | 2 509 412 B |

The 6 091-byte gap is exactly one byte per line: the browser download uses CRLF,
this exporter writes LF. Converting line endings makes the two files identical —
same sha256, `ab871f26…`. LF was kept (the file lives on a Linux server) and the
docstring claim of "byte-comparable" was corrected instead.

### Deployment (done)
- `/usr/local/bin/ct_csv_backup.sh` installed (by the user).
- Block `4b` inserted into `/usr/local/bin/full_backup.sh` before section 5.
  The patched file was prepared in `/tmp`, syntax-checked with `bash -n` and
  diffed before the user applied it with one `sudo install`; the original was
  copied to `full_backup.sh.bak-<timestamp>` first. Root-owned file and
  password-gated sudo meant this step could not be automated.
- The block deliberately leaves `BACKUP_SUCCESS` untouched. That flag gates the
  Google Drive sync and exists to stop a corrupt dump from overwriting a good
  cloud copy. Letting the secondary CSV layer clear it would mean a minor
  failure cancelling the upload of perfectly good database dumps.
- End-to-end check on the server: real run wrote
  `phototraps_data/Roztochya_SNR/RSNR_ct_occurrence_2026-09-02.csv` (2.4 MiB)
  plus `manifest.json`, and `rclone sync --dry-run` confirms both would be
  uploaded to `gdrive_backup:backups/my_server/phototraps_data/`.

### Operational note
Interrupting an `ssh` command locally does not kill the remote process. The
first full dry-run kept running on the server after the local Ctrl+C and had to
be killed by PID. Worth remembering for any long remote job started this way.

### Still open
A full dry-run across all 24 institutions has not been done yet — only RSNR.

## 2026-09-02 (later still) — Export query: scope before aggregating, and make it deterministic

Triggered by the first full backup run: nine parks finished in 47 s, then one
park hung for over nine minutes. The user pushed back on the obvious reading
("Roztochya has the most data, why is a small park slower?"), which was right —
both of my first two explanations were wrong, and each was abandoned only after
a measurement contradicted it.

### Two wrong guesses, for the record
1. **"Too many locations in the EXISTS."** Carpathian BR has 108 locations, the
   most of any institution. It finished in 3 s. Dead.
2. **"Fixed cost of the shared CTEs dominates."** Timed standalone: 375 ms and
   101 ms. Nowhere near the observed minutes. Dead.

What settled it was per-park timing taken from file mtimes (free, no extra load)
plus `pg_stat_activity`: nine parks at 3–11 s each, then the tenth — Tarutynskyi
Step, **two locations** — stuck, CPU-bound, `wait_event` empty, cache hit ratio
excellent. Smallest park, slowest query.

### The actual cause
`EXPLAIN` showed the order of operations. ObservationConsensus and
WinningIdentifiers each aggregate the full `identifications x photos` join
(≈500k x 750k) and AIPick windows over ≈760k `ai_predictions`. Only *after* all
of that was the institution filter applied, as a trailing `Nested Loop Semi
Join` on `location_institutions` estimated at `rows=1` — an estimate that pushed
the planner into nested loops throughout.

So every park paid for aggregating the entire archive, and the more selective
the filter, the worse the plan.

### Fix 1 — ScopedObs
Everything depending only on the observation (date window, location validity,
institution, QC exclusion) moved into a `ScopedObs` CTE evaluated once; the
heavy CTEs join it. Species predicates stayed in the producers — `species` is
not joined at that level.

Measured on production ct_db:

| | before | after |
|---|---|---|
| Tarutynskyi Step (2 locations) | 9+ min | **84 ms** |
| Roztochya (22 383 series) | 23 s | **399 ms** |
| all 24 institutions | never finished | **18 s** |

Note the 23 s figure for Roztochya was itself measured on a warm cache right
after a 2.5-minute scan of the same data — a bad benchmark that flattered the
old code.

### Fix 2 — determinism (found by A/B diffing the output)
Re-running the export produced files that differed from the previous run.
Row-level analysis across ten parks: **zero rows added or removed, and the only
changed field anywhere was `individualCount`**, always on `unverified (AI)` rows
— 147 in Hutsulshchyna, 77 in Roztochya, 73 in Carpathian BR.

The user raised the obvious alternative: the site is live, someone may have been
classifying. Checked rather than assumed — 94 new identifications had indeed
arrived in that window, so the concern was well founded. But `ai_predictions`
had not been written since 7 August, and a new human identification moves a
series out of the AI branch entirely, changing `identifiedBy` and status. Those
fields were untouched. So the AI-row differences could not be new data.

The cause: none of the orderings was total.
- **AIPick** picks one prediction per series, but a series has many photos and
  therefore many predictions. Equal `accuracy_rank` and `prediction_score` left
  the winner — and its `animal_count` — to the plan. Added `ap.id`.
- **RankedConsensus** — equal votes and quantity, arbitrary species. Added
  `species_id`.
- **RankedAggregatedData** (both aggregation modes) — added `observation_id`.
- **Final `ORDER BY series_start_time`** is not a total order. Added
  `observation_id, species_id`.

This matters beyond tidiness: the backup decides "nothing changed" by hashing
the CSV. A non-deterministic query would rewrite every file every night and
rotate the previous version away, defeating both the change check and the
retention.

Verified after the fix: two consecutive full runs, second reports
`0 written, 23 unchanged, 0 failed`, and all 23 files compare byte-identical.

### Validation
- All **72** generated SQL variants (3 export modes x 2 filter types x 3
  aggregations x with/without institution filter, QC flags on) pass `EXPLAIN`
  against the real schema with zero errors.
- Row-level equivalence checked against files produced by the old code and
  against the user's own manual download: 674, 1253, 6090 rows — exact.
- `tests/test_ct_export_scoping.py` — 25 tests reading the generated SQL: the
  scope CTE exists in every mode, each heavy CTE joins it, the institution
  filter appears exactly once, species predicates stay put, and every ordering
  has a tie-break.
- Full suite: **1634 passed, 36 skipped**.

### End-to-end result
23 institutions exported in 18 s, 8.3 MB total, synced to
`gdrive_backup:backups/my_server/phototraps_data/` — 23 park folders next to the
existing `postgres/` and `geoserver/`. One institution (YSSNR) has no occurrence
rows and is correctly skipped.

### Open
- **myproject still points at the old shared-ct commit.** It uses the same
  export code and would benefit identically. Bumping it is a separate change in
  a separate repo.
- The nightly `full_backup.sh` run has not yet exercised block `4b` in place;
  check `full_backup.log` after 02:30 UTC.

### Two operational lessons
- Interrupting `ssh` locally does not kill the remote process; a stray full
  export kept running and had to be killed by PID.
- A waiter written as `while pgrep -f "flask ct-csv-backup"` matches **its own
  command line** and never exits. It looked like the export was hanging when it
  had finished minutes earlier.

## 2026-09-02 — Backup verification and notifications (and a real gap found)

Asked for: a Telegram/Discord message when the nightly backup succeeds, listing
what was backed up and how big it is — but only when everything is genuinely
fine — and, more importantly, a message when it is not.

### `deploy/backup_report.sh`
Verifies artefacts rather than exit codes, which matters because exit codes are
exactly what failed here (see below). Checks, all cheap:

* every non-template database has a dump dated today, plus `globals_roles`;
* archives up to 50 MiB get a real `gzip -t` — CRC32 over the whole stream;
* bigger ones are checked structurally: gzip magic plus a non-zero trailer,
  which is written only when the stream is closed properly. This keeps the
  nightly job from spending minutes unpacking the 900 MB `geodata` dump;
* nothing is 0 bytes, nothing has an mtime younger than 60 s (still being written);
* every camera-trap CSV is re-hashed and compared with the sha256 in its own
  `manifest.json` — ~8 MB total, so this is both cheap and conclusive.

Whole run: **3.5 s**.

`--watchdog` mode covers what the report cannot: `full_backup.sh` never starting,
or dying before it reaches the report. It writes a date stamp on success and
alerts if today's stamp is missing. Cron entry runs 05:00 UTC.

No credentials in the file — the repository is public. They come from
`/etc/alert-webhook.conf`, falling back to `/var/www/biomon/.env`.

### A false positive of my own, worth recording
The first version compared the gzip trailer's ISIZE against the compressed size
and flagged `geodata` as corrupt. ISIZE is the uncompressed size **modulo 2^32**;
geodata unpacks to more than 4 GiB, so the counter wraps. A plausible-looking
check that quietly calls a good backup broken is worse than no check, hence the
size-threshold split above and an explicit comment against reinstating it.

### The real finding: PostgreSQL roles were never being backed up
`globals_roles_20260902_023001.sql.gz` is **20 bytes — zero after
decompression**. Roles, users and passwords are not in any backup.

Chain of causes, each individually reasonable:
1. `sudoers` grants `NOPASSWD` for `/usr/bin/psql` and `/usr/bin/pg_dump` — but
   **not** `pg_dumpall`.
2. In cron there is no tty, so `sudo -u postgres pg_dumpall --globals-only`
   fails immediately.
3. `full_backup.sh` runs it as `pg_dumpall | gzip > file`, and a pipeline's exit
   status is that of the **last** command. `gzip` succeeded, so the `if` branch
   reported success.
4. The follow-up guard is `[ -s "$file" ]` — "not empty". An empty gzip stream is
   20 bytes, so it passed that too.

Fix needs root: add `pg_dumpall` to the NOPASSWD list. The prepared
`full_backup.sh` patch also adds `set -o pipefail`, so a future failure of this
shape reports itself instead of hiding.

### Prepared, waiting on the user (root required)
`/tmp/full_backup.sh.new` on the server — syntax-checked, diffed — adds the
`pipefail` line and a section 6 that calls the report after the rclone sync.
Section 6 deliberately does not touch `BACKUP_SUCCESS`: the backup is already
made by then, and the report only says what came of it.

## 2026-09-02 — Silencing the daily false "GeoServer не відповідає" alert

`disk-alert.sh` runs every five minutes and checks GeoServer over HTTP.
`geoserver_backup_helper.sh` stops the service to tar its data directory, so one
of those runs lands inside the backup window every single night. The alert was
correct about the symptom and useless as a signal.

### Approach: maintenance flag, with an expiry
`full_backup.sh` raises `/home/yura/.backup-state/geoserver-maintenance` around
the helper call and clears it as soon as the service is confirmed back up (plus a
`trap ... EXIT` in case the script dies in between). `disk-alert.sh` skips the
HTTP check while that flag is fresh.

The expiry is the part that matters. A flag with no age limit would mean a backup
that died between raising and clearing it silences GeoServer monitoring
permanently — trading a daily false positive for an indefinite false negative,
which is strictly worse. A flag older than 30 minutes is therefore reported as
its own problem and then removed, restoring the check.

Rejected alternative: skipping the check between 02:25 and 02:45. It couples the
monitor to the backup's schedule, and says nothing if the backup runs long.

### Verified offline before touching the real files
Patched copies in a mode-700 directory (`disk-alert.sh` carries the Telegram
token inline, so it must not be copied anywhere world-readable), with
`send_alert` stubbed to `echo` and the health URL pointed at a dead port:

| flag | result |
|---|---|
| absent | 🔴 GeoServer не відповідає — as before |
| fresh | nothing — check skipped |
| 2 hours old | ⚠️ stale-flag alert **and** the GeoServer alert; flag removed |

Both files pass `bash -n`, and the patcher is idempotent.

### Side finding: operational state was being backed up
`.last_verified_backup` (the report's success stamp) lived inside `$BACKUP_ROOT`,
which `rclone sync` mirrors wholesale — so it had already been uploaded to Drive,
and the maintenance flag would have followed. Operational state is not a backup
artefact. Both moved to `/home/yura/.backup-state/`, outside the mirrored tree;
`BACKUP_STATE_DIR` overrides it. The next sync removes the stray file from Drive
on its own, because `sync` mirrors deletions.


## 2026-09-02 — Registration asks for institutions; managers decide their own

### What the signup form gained
Two fields on `/{lang}/register`:

* **Institutions** (`<select multiple>`, grouped by ecoregion, optional) — the
  territories the applicant wants to work with.
* **Motivation and experience** (free text, optional, max 2000 chars) — the
  question a decider actually needs answered before granting anything.

Neither grants access. The institutions are stored as *requests*, so an
applicant who names two parks still sees public locations only until somebody
approves.

### Data model
New table `verification_request_institutions` (migration `f2a8c31d47b6`): one row
per (verification request, institution), each with its own
`status` / `decided_at` / `decided_by_id`. A row per institution — rather than a
list on the request — is what allows two parks to answer independently and keeps
the audit trail of who granted what.

Same migration adds `verification_requests.applicant_note` (the motivation text,
copied onto every module request of one signup — it is the person speaking, not
a per-module answer).

### Who may decide what
`/admin/verification-requests` is now open to managers as well as admins:

| | admin | manager |
|---|---|---|
| requests visible | all | those naming an institution they have access to |
| institutions actionable | all rows | only their own institutions |
| status filter applies to | the request | *their* row on the request |

`VerificationRequestService.scope_rows()` is the single place that answers "which
rows may this person touch"; `_visible_query()` mirrors it for listings. A
manager POST naming another park's institution id changes nothing for that park
(test: `test_manager_cannot_grant_a_park_that_is_not_theirs`).

The status filter deliberately follows the manager's own row, not the request:
once the Uzhanskyi manager has answered, the applicant leaves *their* pending
list even though the request is still pending for Skole Beskids.

### What approval does now
`decide()` works institution by institution. Ticked institutions are approved
and attached to the user (`user_institutions`, `can_export=False`); unticked ones
in the decider's scope are recorded `rejected`, which is how "remove this
institution from the request" is expressed in the UI. Rows outside the scope are
never touched, so the request stays pending for the other managers. The request
closes (approved if anything was approved, else rejected) only when no row is
left undecided.

The module role (`ct_verifier` / `pam_verifier`) is granted as soon as anything
is approved: a person cleared for one park must not wait for the others. A
request naming no institution behaves exactly as before — one yes/no, role only.

Checkboxes live in the institutions cell and submit with the decide form in the
actions cell via the HTML5 `form` attribute (a `<form>` cannot span table cells).

### Notifications
`send_decision_email` names the institutions this decision opened and stays
silent about those still deciding. `notify_admin_new_requests` now also emails
the managers of the named institutions (`_managers_to_notify`) — otherwise nobody
but the admin would ever learn a request is waiting in their queue.

### Choices worth remembering
* Both new fields are optional. A public form must not refuse an account over an
  unfilled essay, and an applicant who names no institution is simply asking for
  public locations. Making motivation mandatory is one validator in
  `RegistrationForm` if that turns out to be wanted.
* Institution choices are validated against the DB in
  `create_self_registered_user`, not only by the form, so a hand-crafted POST
  cannot create rows pointing at nothing.
* `_build_inst_groups` moved to `app/utils/utils.build_institution_groups`; the
  admin user form and the public registration form now share one grouping.

### Verification
`venv/Scripts/python -m pytest` — 1691 passed, 36 skipped. New file
`tests/test_verification_institutions.py` (21 tests) covers the two-parks
scenario end to end, manager scoping, removal by unticking, and the motivation
field. `tests/test_registration.py` gained a manager case in place of the old
"admin only" parametrisation.

i18n cycle run in full (extract / update / translate en / compile -f). Only the
root catalogs are involved; nothing in the `shared-ct` / `shared-pam` submodules
was touched, and their translation catalogs stay isolated.

### Deploy
1. `flask db upgrade` (creates the table and the column, idempotent).
2. No submodule commits needed — the change is entirely in the biomon root.


## 2026-09-02 (later) — Who gets the "new request" letter

`ADMIN_EMAIL` holds at most one address and is unset in production, so the
letter announcing a confirmed registration reached nobody but the Telegram
channel. Recipients now come from the database instead:

* every active account with the `admin` role, plus
* managers of the institutions named in the request.

`ADMIN_EMAIL` still works as an extra mailbox without an account when set.
An account with no email address is skipped — there is nothing to send to, and
that person sees the request in the queue anyway (this is deliberate: two of the
production managers have no address on file). Deactivated accounts are skipped.
One message per recipient, not one with many To: addresses — the people involved
work for different institutions and need not see each other's addresses. The
recipient list is logged.

### Verified on production
A real test request (`test.zayavka.claude`, Yavorivskyi NNP, photos + sounds)
produced three letters, all accepted by Resend:
`yurastrus@gmail.com` (the only admin account with an address),
`ira.shpakovska@fzs.org` and `despob@outlook.com` (managers holding YNNP).
`admin_ynnp` and `daria.svidzinska` were skipped for having no address.

The queue then showed the request to all four of those deciders, with only YNNP
actionable for `admin_ynnp` and both parks actionable for the wide-access
managers; `volodia.dovhanych` (KBR only) and `Prylutskaa` saw nothing.

### Trap worth remembering
`send_email` hands delivery to a **daemon** thread. That is fine under gunicorn,
which keeps running, but a one-off `venv/bin/python -c …` script exits
immediately and its daemon threads are killed mid-SMTP — the first run of the
prod test queued three letters and delivered none. Any script that sends mail
outside the web process must join the delivery threads before exiting.


## 2026-09-02 (fix) — An all-unchecked checkbox group sends nothing

Reported from production: photos were approved with the park left ticked and
sounds submitted with it unticked, yet both came out `approved`.

Cause was in the route, not the data model. A checkbox group with every box
unchecked sends **no field at all**, and `decide_verification_request` read a
missing `institutions` as "no selection given, approve everything in my scope".
So the sounds request, submitted with nothing ticked, was approved in full.

The form now carries a hidden `institutions_present` marker, rendered only when
the decider actually has actionable institution rows. With the marker, the
(possibly empty) list is the answer; without it — a request naming no
institution, or an older cached page — the previous fallback still applies.
"Approve" with nothing ticked is also reported as a decline now, because that is
what it does.

Verified on production by resetting the test account and redoing both decisions
through the real form: `ct` → approved + YNNP granted, `pam` → rejected, and no
`pam_verifier` role. Tests: 4 new cases, and every decide POST in the suite now
sends the marker so the tests match the real page.

### Known limit of the access model (not a bug, a design boundary)
`user_institutions` has no module column: one row means "this person may see
this institution's data", in both camera traps and PAM. Both modules read
`user.institutions` (86 call sites: 63 in shared-ct, 19 in shared-pam, plus the
digest). What *is* per module is the role — `ct_verifier` / `pam_verifier`.

So "photos in Yavorivskyi, but not sounds in Yavorivskyi" is expressible only
while the person has no `pam_verifier` role at all. Once some other park grants
them PAM rights, the shared institution row lets them verify sounds in
Yavorivskyi too. Making that precise needs a module dimension on the grant
(a `can_ct` / `can_pam` pair on `user_institutions`, or a row per module) and a
matching filter change in both submodules — see the report to the user.
