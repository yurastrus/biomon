# biomon error-message and logging audit (2026-11-04)

Status: **Step 1 (analysis) + Step 2 (convention) done. Step 3 (implementation) waits for review.**
No application code was changed while producing this audit.

Goal (from the planning task): every failure anywhere on biomon must give the user a concrete,
actionable message and leave one greppable, contextual log line (with traceback for 5xx). The only
place that met this bar was `camera_traps/upload-fast` (shared-ct `22b0965`, biomon `bdb45db`,
09.07.2026); this audit checks the rest of the site and plans the rollout.

Detailed registries (file:line, P1/P2/P3) are in the appendices:

| File | Scope | Rows | P1 |
|---|---|---|---|
| [A_core_infra_admin_sdm_auth.md](A_core_infra_admin_sdm_auth.md) | logging config, Flask error handlers, admin, auth, sdm, utils, backup | 64 | 15 |
| [B_camera_traps_server.md](B_camera_traps_server.md) | `app/camera_traps/*.py` | 58 | 10 |
| [C_pam_server.md](C_pam_server.md) | `app/pam/*.py` | 93 | 25 |
| [D_client_js.md](D_client_js.md) | 113 AJAX call sites in 40 templates/JS files | 113 | 33-34 |

P1 = user sees nothing useful AND nothing is logged, or silent data loss, or internal error text leaked to UI.
P2 = logged but without traceback/context, or generic message. P3 = cosmetic/consistency.

Key claims were spot-checked against the code (CT `utils.py:620-633` + `routes.py:2727` DEBUG log,
`daily_analytics.py:156` bare except, `extensions.py` unauthorized/CSRF handlers, `basicConfig` in both
analytics calculators, `TRUSTED_PROXY_COUNT` default 0).

---

## 1. Root causes (site-wide, fix once)

1. **Logging is not configured.** No handler/format/level is set in `create_app`. In production the
   effective level is WARNING, so every `logger.info` audit line (registration, password change,
   verifier grants, verification decisions) is dropped. Worse, `analytics_calculator.py:23` (CT) and
   `pam_analytics_calculator.py:13` call `logging.basicConfig(INFO)` at import time; the CT one is
   imported lazily inside a worker, so log level and duplication differ between the 3 gunicorn
   workers. SDM worker loggers (`adapters.*`) reach only Python's last-resort handler.
2. **No global error handlers** except CSRFError. 404/405/413/429/500 and unhandled exceptions return
   Werkzeug's English HTML pages, also to AJAX callers. No `templates/errors/`.
3. **Expired session on AJAX = 302 to login page.** `fetch` follows it, gets 200 HTML, and ~55 call
   sites crash in `r.json()` with "Unexpected token '<'". Only `identification.html` handles it.
4. **CSRF JSON response only for `is_json` or `/api/`** paths, so FormData/multipart uploads get HTML 400.
5. **No request id / user in log records**, so a user's screenshot cannot be tied to a log line.
6. **No shared client helper.** `describeAjaxError` lives only in `upload_fast.html`; there are ~9
   page-local `showError` variants, 2 byte-identical `getJSON`, 3 copies of a run-and-poll block, and
   5 different ways to read the CSRF token.

## 2. Highest-value specific findings (P1 shortlist)

Data integrity / silent "success":
- CT `utils.py:620-633`: every error during photo processing (DB, disk-full) is re-raised as
  `ValueError` with raw text; the upload-fast route logs ValueError at **DEBUG** (`routes.py:2727`)
  and returns 400. A repeat of the 07.08 disk-full incident would leave no `[ct-upload]` line.
- CT grouping (`utils.py:722`, `747-751`): failed photos skipped, batch marked completed; whole-batch
  failure status is set and then rolled back, so nothing is recorded.
- CT `daily_analytics.py:156`: bare `except` turns any failed KDE into a zero curve, feeding bootstrap
  CIs and plotted as "no activity". Scientific-output risk, not just UX.
- PAM CSV import (`pam_import_utils.py:185, 280, 386`) and ZIP upload (`pam_upload_utils.py:279, 453`)
  drop rows/whole species folders while the user sees success.
- PAM `cleanup_completed_verifications`: file deleted before the DB status update; a DB error leaves
  the file gone and status `completed`.
- PAM analytics calculator falls back to empty institution/effort maps and still saves results; batch
  job exits 0 on failure; `biotope_autoassign.py:521` `except: pass` can leave status stuck in running.
- SDM: final job-status update unguarded (job stuck in `running`); import step reports success with no
  prediction CSVs, next step marks the empty run current, map goes blank. 4 helpers in
  `sdm/routes.py` `except: return []`.
- Admin: `edit_institution`/`edit_role` flash success before `commit()`; `edit_user` sends mail inside
  the same try, so it can report failure after a successful save.

Leaks / wrong codes:
- ~10 admin handlers + CT/PAM cleanup flashes show raw `str(e)` (SQL/OS text) to users.
- PAM `/audio/segments`, `/audio/spectrograms`: `abort(403/404)` inside `except Exception` becomes 500
  with ERROR log (403 from path confinement is lost; by-design missing archived files spam ERROR).
- SDM job status endpoint returns full Python tracebacks to the browser.
- Data helpers (24 in PAM, several in CT/SDM) return `[]`/`{}` on DB error, so dashboards answer 200
  with "no data" / "not yet calculated".

Client:
- 4 state-changing POSTs ignore HTTP status (CT `admin.html:680, 767`, `pam_admin.html:128`,
  `gap_analysis.html:709`): failure looks like success.
- Infinite polling with no stop on error: CT `admin.html:552` (2 s), `sdm/dashboard.html:228` (3 s).
- No error handler at all: `ct_data_export.html:135`, `pam_data_export.html:136`,
  `pam_yearly_table.html:149`, `sdm/dashboard.html:248`, `pam_verification_interface.html:294`.
- `upload_fast.html:972` discards resume state on any transient 502.

Unguarded user-facing endpoints: CT `/api/next-observation-for-identification` (HTML 500 on DB error),
PAM `/pam/api/location/<id>` (500 with no log), 18 PAM 5xx handlers without traceback.

## 3. Convention (Step 2 proposal)

### 3.1 Server

- **One logging setup** `app/logging_setup.py`, called first in `create_app`: root StreamHandler to
  stderr (gunicorn → journald), level from `LOG_LEVEL` (default INFO), a `RequestContextFilter`, format
  `%(asctime)s %(levelname)s %(name)s [rid=… uid=… ip=… METHOD path] %(message)s`. Installing the root
  handler first makes the stray `basicConfig()` calls no-ops; the calls themselves are removed later in
  the shared repos.
- **Request id**: `g.request_id` from `X-Request-ID` (nginx `$request_id`) or a short uuid; echoed in
  response header, JSON errors and error pages ("Код: ab12cd34ef56"). A user screenshot then maps to
  `journalctl -u biomon | grep ab12cd34ef56`.
- **Global handlers** `app/errors.py`: `wants_json()` (is_json, `/api/`, `X-Requested-With`, Accept),
  `HTTPException` handler (WARNING `[http]` for 4xx), catch-all `Exception` handler (rollback +
  `logger.exception('[unhandled] …')`). JSON shape everywhere: `{"error": <user text>, "code": <slug>,
  "request_id": <rid>}`. HTML: translated `templates/errors/{400,403,404,413,429,500}.html` extending
  `base.html`; 500 page must not touch DB.
- `unauthorized()` returns `401 {"code":"login_required"}` when `wants_json()`; CSRF handler uses the
  same `wants_json()` and logs `[csrf]` at WARNING.
- **Helper** `log_and_respond(tag, exc, status=500, *, user_message=None, redirect_to=None, level=ERROR, **ctx)`
  in `app/utils/errors.py`: rollback, one log line `[tag] ExcName: msg k=v …` (traceback for ERROR),
  JSON or flash+redirect. Shared submodules import it guarded (fallback copy) since myproject must
  provide the same helper.
- **Levels**: 5xx / unexpected → `logger.exception` (traceback). Expected 4xx (validation, not found,
  forbidden, duplicates) → WARNING with context, no traceback. Never DEBUG for anything a user can see.
  Silent fallbacks are allowed only for genuine parse fallbacks and must say so in a comment.
- **User text**: concrete and translated; never raw `str(e)` from DB/OS. Append `[ExcName]` and the
  request id for 5xx (upload-fast style). Known domain errors keep their precise message.
- **Status codes**: user-input errors 4xx, server faults 5xx; `abort()` never inside a broad `except`
  (or re-raise `HTTPException` first).
- **Tags**: one per functional area, prefixed by module. Existing tags keep their names.
  - core: `[http] [unhandled] [csrf] [auth] [admin] [backup]`
  - CT (~18, mapped to routes in appendix B §3): `[ct-upload] [ct-video] [ct-csv-backup] [ct-manage]
    [ct-identify] [ct-export] [ct-analysis] [ct-gap] …`
  - PAM (appendix C §3): `[pam-dashboard] [pam-verify] [pam-verify-upload] [pam-media] [pam-import]
    [pam-sample] [pam-locations] [pam-export] [pam-gap] [pam-cooc] [pam-admin] [pam-maint]
    [pam-analytics-calc] [pam-biotope-autoassign] …`
  - SDM: `[sdm-job] [sdm-import] [sdm-api]` (appendix A §3).

### 3.2 Client

- New vanilla-JS `app/static/js/http_errors.js` exposing `window.HttpErr`: `csrf()`, `fetchJSON()`
  (CSRF header, `X-Requested-With`, Content-Type check, login-redirect / `csrf_expired` → `err.stale`),
  `describe(err, fallback)` (same `[HTTP 413] …` output as `describeAjaxError`, accepts jqXHR,
  fetch errors, network `TypeError`), `log(ctx, err, extra)` → one `console.error('[biomon][ctx] …')`,
  `poll(fn, {intervalMs, maxFails})`, `showStale()` banner with reload button. Optional global
  `$(document).ajaxError` that only logs, so un-migrated jQuery calls leave a console line.
- Included in **each host's** `base.html` (biomon and myproject) outside `{% block scripts %}`, with
  translated strings in `window.HTTP_I18N`. Not in a submodule's static dir (would make PAM/SDM depend
  on CT).
- `upload_fast.html` helpers become thin aliases.

## 4. Implementation plan (Step 3, after review)

Order is driven by the shared submodules: host-side infrastructure must be deployed in **both** biomon
and myproject before submodule code starts depending on it.

| Phase | Where | What | Size |
|---|---|---|---|
| 0 | server `.env` / nginx (server-ops) | Check `TRUSTED_PROXY_COUNT` (else every logged IP is 127.0.0.1), nginx `client_max_body_size`, add `proxy_set_header X-Request-ID $request_id` | 0.5 h |
| 1 | biomon core | `logging_setup.py`, request id, `errors.py` handlers + error templates, JSON 401 / CSRF fix, `log_and_respond`, `MAX_CONTENT_LENGTH` aligned with nginx, `http_errors.js` + base include; tests via `test_client` | 3-4 h |
| 1b | myproject core | Same infrastructure (same files) | 1-2 h |
| 2 | shared-ct | P1 data-integrity: `utils.py` processing error classes + log levels, grouping status, `daily_analytics` KDE, identification endpoint guard, raw `str(e)` flashes; remove `basicConfig` | 3 h |
| 3 | shared-pam | P1: CSV/ZIP import reporting, cleanup ordering, analytics fallbacks + job exit code, autoassign, media `abort` inside except, location API; remove `basicConfig` | 3-4 h |
| 4 | shared-sdm + biomon admin | SDM job status guard, empty-import detection, traceback leak; admin commit-before-flash, `log_and_respond` | 2 h |
| 5 | templates (CT/PAM/SDM/admin) | Client migration in the priority order of appendix D §3 (P1 writes → P1 silent loads → upload paths → rest), delete 2 dead templates | 4-6 h |
| 6 | all | P2/P3 sweep: tags, traceback on remaining 5xx, WARNING for logged 4xx | 2-3 h |

Each phase is small commits; tests as in the reference (template render + inline-JS syntax, error
branches via `test_client`, live `journalctl -u biomon | grep '[tag]'` after deploy). Restart after
deploy needs interactive sudo (left to the owner).

## 4a. Progress log

**Review 2026-11-04:** owner approved all four open questions (JSON shape with request id, INFO in
production, recalc+diff of affected analytics after the fixes, delete the two dead templates).

**Phase 0 (2026-11-04), server checks, read-only:**
- `TRUSTED_PROXY_COUNT=1` in `/var/www/biomon/.env`, and nginx `location /` includes `proxy_params`
  (sets `X-Forwarded-For`), so logged IPs and rate-limit keys are real client IPs. OK.
- nginx `client_max_body_size 3096M` (biomon-app and myproject). Flask `MAX_CONTENT_LENGTH` stays
  unset on purpose: nginx is the effective limit and large CT/PAM uploads must not start failing.
- nginx does not send `X-Request-ID`. The app generates its own id, so this is optional. To share
  the id with the nginx access log, add inside `location /` of `/etc/nginx/sites-enabled/biomon-app`:
  `proxy_set_header X-Request-ID $request_id;` then `sudo nginx -t && sudo systemctl reload nginx`
  (needs sudo, left to the owner).

**Phase 1 (2026-11-04), biomon core, implemented, not deployed:**
- `app/logging_setup.py`: one root handler, format with `rid/uid/ip/path`, `LOG_LEVEL` (default
  INFO), Flask default handler removed (no duplicate lines), stray `basicConfig()` neutralised.
  Skipped under TESTING so pytest's capture keeps working.
- `app/errors.py`: request id (`X-Request-ID` in/out, validated), global `HTTPException` and
  `Exception` handlers (JSON or translated `templates/errors/error.html`, fallback bare HTML if the
  page itself fails to render), `wants_json()`, `log_and_respond()`. Debug/testing still propagate
  exceptions, so existing tests behave exactly as before.
- CSRF handler: JSON also for XHR (jQuery/FormData), page requests get the site error page; logged
  as `[csrf]` WARNING.
- `app/static/js/http_errors.js` (`window.HttpErr`) + include and `HTTP_I18N` in `base.html`. No page
  uses it yet; the only visible effect is a console line for every failed jQuery request.
- en translations for all new strings; `babel.cfg` now also scans `app/errors.py`, `app/extensions.py`.
- **Deferred to Phase 5:** JSON `401` for AJAX in `unauthorized()`. `identification.html` treats a
  non-JSON 401/redirect as "stale page" but a JSON 401 as an ordinary error, so switching now would
  degrade its UX. Switch together with the identification/HttpErr migration.
- **Note for Phase 3:** with INFO on, PAM routes emit many chatty INFO lines per page view
  (`Loading verification upload page...`, `Attempting to connect...`). Demote those to DEBUG there.
- Tests: `tests/test_error_infra.py` (25), browser harness `scripts/error_infra_ui_harness.py`
  (port 5082, 13 checks against real responses, all PASS).

## 5. Open questions for review (answered 2026-11-04: yes to all)

1. Accept the JSON error shape `{error, code, request_id}` and showing the request id to users?
2. Default `LOG_LEVEL=INFO` in production (more journald volume, but audit lines appear)?
3. Phase 2-4 touch analytics correctness (KDE zero curves, empty institution/effort maps): after fixing,
   should we check whether already published/stored analytics were affected (recalculate + diff)?
4. OK to delete `camera_traps/templates/service_log.html` and `pam/templates/pam_service_log.html`
   (dead, broken URLs / no route)?
