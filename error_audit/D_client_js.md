# biomon client-side AJAX error handling audit

Date: 2026-11-04. Scope: `app/camera_traps`, `app/pam`, `app/sdm`, `app/templates` (incl. `admin/`), `app/static`
(*.html, *.js; minified/vendor skipped). Read-only; nothing was edited.
`app/static` holds only `css/` and `img/` (no JS). `app/admin` has no templates with AJAX (admin pages live in `app/templates/admin/`).

Reference implementation: `app/camera_traps/templates/upload_fast.html:507-527`
(`describeAjaxError` + `logClientError`). A second decent pattern exists in
`camera_traps/templates/identification.html:496-512` (`isStaleResponse` + `readJson`: checks Content-Type,
handles login redirect and the server's `csrf_expired` code).

## Severity rule used

- **P1**: silent failure (no UI change) with no console trace, or bare generic text ("Помилка...") with no console trace,
  or a state-changing request whose HTTP status is ignored (failure looks like success), or polling that never stops on error.
- **P2**: server message shown for JSON errors, but non-JSON bodies (CSRF 400 HTML, 413, nginx 502/504, login-redirect HTML)
  and/or network errors degrade to "Unexpected token '<'..." / a generic text / a misleading "no connection" message;
  or the UI is silent but a `console.error` exists; or no console logging on an otherwise informative path.
- **P3**: `alert()` as the only channel, cosmetic, or intentionally silent non-critical fallback.
- **OK**: matches the reference (HTTP code + server text + non-JSON + network + console).
- **W** in the table = the request changes state (save / delete / upload / run).

## Cross-cutting root causes (read these first)

1. **Session expiry breaks almost every `fetch().then(r => r.json())`.** `app/extensions.py:24-29` (`unauthorized_handler`)
   redirects to the login page; fetch follows the 302 and gets `200 text/html`, so `r.ok` is true and `r.json()` throws
   `SyntaxError: Unexpected token '<'`. Only `identification.html` detects this (`response.redirected && url.includes('/login')`).
   Server-side fix worth doing alongside: return `401 {"code":"login_required"}` for XHR / `/api/` requests.
2. **CSRF expiry returns JSON only for `request.is_json` or `/api/` paths** (`app/extensions.py:31-39`). Multipart POSTs
   outside `/api/` (e.g. `pam.process_verification_upload`, CT upload endpoints that are not under `/api/`) get Werkzeug's HTML 400.
3. **Server error key is inconsistent**: some endpoints use `error`, others `message` (e.g. CT `/api/create-location` returns
   `message`). A helper must read both, as `describeAjaxError` does.
4. **No global handler.** No `$.ajaxSetup`, no `$(document).ajaxError`, no `window.onunhandledrejection`. jQuery is not loaded
   in `base.html`; every page loads its own copy.
5. **Fetch without `.catch`** (ct_data_export:135, pam_data_export:136, pam_yearly_table:149, sdm/dashboard:248) produces
   unhandled promise rejections and leaves selects stuck on "Завантаження...".
6. **Dead templates with AJAX**: `camera_traps/templates/service_log.html` (route `/service-log` now redirects to
   manage_locations; its URLs at :166 and :245 use the invalid `{_ g.lang_code _}` syntax and would 404) and
   `pam/templates/pam_service_log.html` (not rendered anywhere). Recommend deleting both rather than migrating; excluded from stats.

## 1. Summary stats per module

| Module | Files with AJAX (live) | Call sites audited | P1 | P2 | P3 | OK | Files with `console.error/warn` | `alert(` calls (all, incl. validation) |
|---|---|---|---|---|---|---|---|---|
| camera_traps | 18 (+1 dead) | 60 | 15 | 33 | 7 | 5 | 7 / 18 | 60 |
| pam | 19 (+1 dead) | 47 | 14 | 32 | 1 | 0 | 7 / 19 | 22 |
| sdm | 2 | 4 | 2 | 1 | 1 | 0 | 0 / 2 | 2 |
| templates/admin | 1 | 2 | 2 | 0 | 0 | 0 | 0 / 1 | 0 |
| app/static | 0 | 0 | - | - | - | - | - | - |
| **Total** | **40 (+2 dead)** | **113** | **33** | **66** | **9** | **5** | **14 / 40** | **84** |

Out of scope but noticed: `app/docs/static/docs-editor.js:16` (image upload: `r.json()` before status check, generic
`uploadFailed`) and `:44` (preview: silent `catch`). Both P2/P1 by the same rule.

Pattern frequency across the 108 non-OK rows:
- `r.json()` called without checking `r.ok` and/or Content-Type: ~55 sites.
- `.then(r => r.json().then(d => ({ok: r.ok, d})))` copy-paste (breaks on non-JSON): 10 sites.
- `xhr.responseJSON ? xhr.responseJSON.error : generic` (no status, no non-JSON text): 10 sites.
- Empty / comment-only catch: 9 sites (`.catch(() => {})` or `catch (e) { /* not critical */ }`).
- Request status ignored on a state-changing POST: 4 sites (admin.html:680, :767; pam_admin.html:128; gap_analysis.html:709).

## 2. Registry

| # | file:line | call (URL / purpose) | error path behaviour | problem class | sev |
|---|---|---|---|---|---|
| **camera_traps** |||||
| 1 | camera_traps/templates/activity_heatmap.html:381 | `$.getJSON` heatmap data | `.fail` -> `alert("Помилка завантаження даних.")`; `data.error` alerted | bare generic, alert, no console | P1 |
| 2 | camera_traps/templates/admin.html:474,485 | `apiPost`/`apiGet` helpers (cleanup analyze/execute) | `r.json().catch(()=>({}))`, throws `data.error \|\| r.statusText`; UI `showError(e.message)` | non-JSON -> only statusText ("Bad Request"), network -> "Failed to fetch", no console | P2 |
| 3 | camera_traps/templates/admin.html:552-578 | `pollTask` every 2 s via `apiGet` | catch ignores everything except 404 | infinite polling on 5xx/network/session expiry, silent, no backoff | P1 |
| 4 | camera_traps/templates/admin.html:657 | recalc status poll (3 s interval) | no `r.ok`; `catch { /* silently ignore */ }` | silent by design; poll keeps running while `running` | P2 |
| 5 | camera_traps/templates/admin.html:680 | W recalc run POST | response status ignored ("202 or 409, poll both"); only network error shows generic text | 403/400 CSRF/500 look like "started" | P1 |
| 6 | camera_traps/templates/admin.html:751 | biotope auto-assign status poll | as #4 | silent | P2 |
| 7 | camera_traps/templates/admin.html:767 | W biotope auto-assign run POST | response not even read | failure looks like success | P1 |
| 8 | camera_traps/templates/admin.html:815 | AI-pause flag poll (30 s) | no `r.ok`; catch shows "стан невідомий" | no console; acceptable fallback | P3 |
| 9 | camera_traps/templates/behavior_analysis.html:211 | fetch behaviour data | no `r.ok`; catch `alert("Помилка завантаження даних.")` | bare generic, alert, no console | P1 |
| 10 | camera_traps/templates/comparison.html:146 | fetch comparison data | checks `r.ok` but error branch does `r.json()` -> SyntaxError on HTML; `showError(err.message)` | non-JSON shows "Unexpected token '<'", fallback literal 'Error', no console | P2 |
| 11 | camera_traps/templates/ct_data_export.html:135 | taxonomic filter cascade | no `r.ok`, **no catch** | unhandled rejection, selects stuck | P1 |
| 12 | camera_traps/templates/ct_data_export.html:283 | data preview | ok ? json : json->reject; shows `error.error \|\| 'Unknown error'` | non-JSON -> "Unknown error" (status lost); console present | P2 |
| 13 | camera_traps/templates/daily_activity.html:263 | `$.getJSON` activity data | `.fail` -> `alert("Помилка завантаження даних.")` | bare generic, alert, no console | P1 |
| 14 | camera_traps/templates/dashboard.html:223 | chart data | no `r.ok`; console only | UI silent (empty chart) | P2 |
| 15 | camera_traps/templates/dashboard.html:290 | contributors list | `r.ok` checked; console + friendly message | no status/server text | P3 |
| 16 | camera_traps/templates/dashboard.html:330 | map locations | no `r.ok`; console only | UI silent (empty map) | P2 |
| 17 | camera_traps/templates/gallery.html:175 | gallery photos per species | `r.ok`; error body `r.json()` breaks on HTML; shows `error.message \|\| generic`; console | non-JSON -> SyntaxError text | P2 |
| 18 | camera_traps/templates/gallery.html:309 | W remove from favourites | as #17; `alert(error.error \|\| generic)` | non-JSON -> generic; alert | P2 |
| 19 | camera_traps/templates/gap_analysis.html:429 (+callers 664, 723, 762, 777) | local `getJSON` (grid, kernel, cell, biotopes) | `throw new Error(r.status)`; callers show `T.error` | bare generic, status thrown away, no console | P1 |
| 20 | camera_traps/templates/gap_analysis.html:698 | GBIF status poll (5 s) | catch stops timer, re-enables button, no message | silent stop | P1 |
| 21 | camera_traps/templates/gap_analysis.html:709 | W GBIF refresh POST | response ignored; `catch { /* poll reports */ }` | 403/409/CSRF invisible | P1 |
| 22 | camera_traps/templates/identification.html:558 | remaining-count | `r.ok`; console only | counter silently stale | P2 |
| 23 | camera_traps/templates/identification.html:576 | review filters | no `r.ok`; console only | filters silently empty | P2 |
| 24 | camera_traps/templates/identification.html:682 | AI species list refresh | `r.ok`; `console.warn`, keeps old list | acceptable | P3 |
| 25 | camera_traps/templates/identification.html:1066 | next observation (`readJson`) | Content-Type check, stale/login detection, HTTP code | good | OK |
| 26 | camera_traps/templates/identification.html:1656 | W flag series POST | stale handled; otherwise `alert("Помилка позначення серії.")` | bare generic, status/body ignored, no console | P1 |
| 27 | camera_traps/templates/identification.html:1707 | W submit identification | `readJson`; `alert(saveError + error.error\|\|message)` | alert only, no console | P3 |
| 28 | camera_traps/templates/import_classification.html:250 | AI/verification import preview | `r.json()` before ok -> HTML throws; `showError(e)` with Error object | non-JSON -> "SyntaxError..."; literal 'error' fallback; no console | P2 |
| 29 | camera_traps/templates/import_classification.html:292 | W import run | as #28 | as #28 | P2 |
| 30 | camera_traps/templates/manage_deployments.html:599 | load deployment for edit | no `r.ok`; `alert(data.error)`; catch `alert("Помилка завантаження.")` | bare generic, no console | P1 |
| 31 | camera_traps/templates/manage_deployments.html:673 | W save deployment | no `r.ok`; `!resp.success -> Error(resp.error)`; alert | non-JSON -> SyntaxError text; no console | P2 |
| 32 | camera_traps/templates/manage_deployments.html:709 | W delete deployment | as #31 | as #31 | P2 |
| 33 | camera_traps/templates/manage_locations.html:504 | service statuses | no `r.ok`; console | UI silently falls back | P2 |
| 34 | camera_traps/templates/manage_locations.html:547 | load location for edit (click) | `data.error` ignored; `.catch(() => {})` | completely silent | P1 |
| 35 | camera_traps/templates/manage_locations.html:585 | W location validity | `d.error \|\| "Помилка."`; catch "Помилка." | non-JSON/network -> bare "Помилка." | P2 |
| 36 | camera_traps/templates/manage_locations.html:661 | W update location | no `r.ok`; `Error(response.error)`; alert | non-JSON -> SyntaxError; no console | P2 |
| 37 | camera_traps/templates/manage_locations.html:707 | W create location | as #36 | as #36 | P2 |
| 38 | camera_traps/templates/manage_locations.html:746 | W delete location | as #36 | as #36 | P2 |
| 39 | camera_traps/templates/manage_locations.html:769 | service history (click) | no `r.ok`; catch "Помилка завантаження." | bare generic, no console | P1 |
| 40 | camera_traps/templates/manage_locations.html:871 | W save/edit service visit | `{ok,d}` pattern; alert | non-JSON -> SyntaxError; no console | P2 |
| 41 | camera_traps/templates/species_dashboard.html:180 | species analytics | `r.ok`; console + fixed generic text | server message ignored | P2 |
| 42 | camera_traps/templates/species_detailed.html:175 | `$.getJSON` species data | `.fail` -> `alert("Помилка отримання даних")`, legend "Помилка" | bare generic, alert, no console | P1 |
| 43 | camera_traps/templates/upload.html:415 | location coords | `.fail` hides row | non-critical | P3 |
| 44 | camera_traps/templates/upload.html:441 | W create location | `responseJSON ? .message : "Помилка"` | non-JSON -> bare "Помилка", no status, no console | P2 |
| 45 | camera_traps/templates/upload.html:540 | W finalize/grouping | `responseJSON ? .error : generic`; alert | as #44 | P2 |
| 46 | camera_traps/templates/upload.html:597 | W per-file upload | per-row "помилка: " + `responseJSON.error` | 413/502 -> "помилка" without code; no console | P2 |
| 47 | camera_traps/templates/upload.html:621 | W create batch | as #45 | as #45 | P2 |
| 48 | camera_traps/templates/upload_fast.html:714 | location coords | `.fail` hides row | non-critical | P3 |
| 49 | camera_traps/templates/upload_fast.html:729 | W create location | `describeAjaxError` + `logClientError` | reference | OK |
| 50 | camera_traps/templates/upload_fast.html:972 | resume-banner batch status | `.fail(() => clearResumeState())` | a transient 502/network error permanently deletes the resume state | P2 |
| 51 | camera_traps/templates/upload_fast.html:1030 | W per-file upload pool | retry with `isRealDuplicateError` classification | reference | OK |
| 52 | camera_traps/templates/upload_fast.html:1155 | batch-status poll | tolerates transient errors, gives up after `MAX_POLL_FAILS`=15, logs | reference | OK |
| 53 | camera_traps/templates/upload_fast.html:1210,1222,1241 | W create batch / uploaded list / finalize-async | caught at :1263 by `logClientError` + `describeAjaxError` | reference | OK |
| 54 | camera_traps/templates/upload_video.html:615 | W camera calibration | local `errorText(xhr, fallback)` (:342) = `responseJSON.error \|\| fallback` | no status, non-JSON -> fallback, no console | P2 |
| 55 | camera_traps/templates/upload_video.html:688 | header metadata probe | catch sets `meta = null` | intentional fallback | P3 |
| 56 | camera_traps/templates/upload_video.html:824 (+865, 874) | W read/sign clip timing | `errorText` | as #54 | P2 |
| 57 | camera_traps/templates/upload_video.html:899 | W create batch | `errorText` | as #54 | P2 |
| 58 | camera_traps/templates/upload_video.html:963 | W frame upload | per-clip `errorText` | as #54 | P2 |
| 59 | camera_traps/templates/upload_video.html:986 | W finalize | `errorText` | as #54; also receives Error from #60 -> server `error_message` lost | P2 |
| 60 | camera_traps/templates/upload_video.html:1041 | batch-status poll | first failed GET rejects (`'status unavailable'`); `failed` rejects with literal `'failed'` | no tolerance for transient errors (unlike #52), server `error_message` dropped, English text | P2 |
| **pam** |||||
| 61 | pam/templates/manage_pam_locations.html:590 | service statuses | no `r.ok`; console | UI silent fallback | P2 |
| 62 | pam/templates/manage_pam_locations.html:705 | load location for edit | `.catch(() => {})`, `data.error` ignored | completely silent | P1 |
| 63 | pam/templates/manage_pam_locations.html:729 | service forecast | no `r.ok`; "Помилка завантаження." | bare generic, no console | P1 |
| 64 | pam/templates/manage_pam_locations.html:817 | W update location | no `r.ok`; `Error(response.error)`; alert | non-JSON -> SyntaxError; no console | P2 |
| 65 | pam/templates/manage_pam_locations.html:860 | W create location | as #64 | as #64 | P2 |
| 66 | pam/templates/manage_pam_locations.html:900 | W delete location | as #64 | as #64 | P2 |
| 67 | pam/templates/manage_pam_locations.html:923 | service history | no `r.ok`; catch "Помилка завантаження." | bare generic, no console | P1 |
| 68 | pam/templates/manage_pam_locations.html:995 | W delete service visit | `{ok,d}` pattern; alert | non-JSON -> SyntaxError | P2 |
| 69 | pam/templates/manage_pam_locations.html:1092 | W save service visit | as #68 | as #68 | P2 |
| 70 | pam/templates/pam_admin.html:112 | biotope status poll | silent catch (copy of CT #6) | silent | P2 |
| 71 | pam/templates/pam_admin.html:128 | W biotope run POST | response not read (copy of CT #7) | failure looks like success | P1 |
| 72 | pam/templates/pam_cooccurrence.html:313 | filter options | no `r.ok`; `.catch(() => {})` | completely silent | P1 |
| 73 | pam/templates/pam_cooccurrence.html:399 | co-occurrence run | `{ok,d}`; catch -> "Не вдалося звʼязатися з сервером." | HTML 4xx/5xx reported as "no connection" (misleading); no console | P2 |
| 74 | pam/templates/pam_cooccurrence.html:797 | sweep run | as #73 | as #73 | P2 |
| 75 | pam/templates/pam_data_export.html:136 | taxonomic filter cascade | no `r.ok`, **no catch** (copy of CT #11) | unhandled rejection | P1 |
| 76 | pam/templates/pam_data_export.html:285 | data preview | copy of CT #12 | as #12 | P2 |
| 77 | pam/templates/pam_evaluation_results.html:258 | `$.ajax` detailed results | `responseJSON ? .error : generic` into container | no status, no console | P2 |
| 78 | pam/templates/pam_gap_analysis.html:331 (+callers 545, 561, 606, 620) | local `getJSON` (byte copy of CT #19) | `Error(r.status)`; `T.error` | bare generic, no console | P1 |
| 79 | pam/templates/pam_import.html:512 | W create location | no `r.ok`; `Error(resp.error \|\| 'Server error')` | non-JSON -> SyntaxError | P2 |
| 80 | pam/templates/pam_import.html:668 | W BirdNET CSV batch import | `resp.json()` before status; catch -> "Помилка з'єднання: " + err.message | likely 413/CSRF/504 reported as "connection error: Unexpected token '<'"; no console | P2 |
| 81 | pam/templates/pam_overview.html:266 | trends filters | no `r.ok`; console only | UI silent | P2 |
| 82 | pam/templates/pam_overview.html:374 | overview stats | `r.ok`; console + generic | server message ignored | P2 |
| 83 | pam/templates/pam_overview.html:397 | locations map | as #82 | as #82 | P2 |
| 84 | pam/templates/pam_overview.html:496 | species ranking | as #82 | as #82 | P2 |
| 85 | pam/templates/pam_sample_upload.html:246 | species for locations | no `r.ok`; option text "Помилка завантаження видів" | bare generic, no console | P1 |
| 86 | pam/templates/pam_sample_upload.html:339 | W prepare sample | `data.error` shown; HTML -> SyntaxError | non-JSON not handled; no console | P2 |
| 87 | pam/templates/pam_segment_window.html:153 | segment plan | `r.json()` before `!r.ok` check; catch -> "Не вдалося звʼязатися з сервером." | HTML error reported as "no connection" | P2 |
| 88 | pam/static/js/segment_cutter.js:172 | W segment upload | `r.json()` without ok; returns `data.error \|\| r.status` or `e.message` | non-JSON -> SyntaxError text; no console | P2 |
| 89 | pam/templates/pam_species_dashboard.html:100 | institutions filter | `r.ok`; console only | UI silent | P2 |
| 90 | pam/templates/pam_species_dashboard.html:155 | species analytics | `r.ok`; console + generic | server message ignored | P2 |
| 91 | pam/templates/pam_species_detailed.html:382 | dynamic filters | `r.ok`; console only | UI silent | P2 |
| 92 | pam/templates/pam_species_detailed.html:422 | evaluation thresholds | `r.ok ? json : {}`; console | intentional fallback | P3 |
| 93 | pam/templates/pam_species_detailed.html:546 | species summary | `r.ok`; `getErrorHtml("Помилка статистики.")` | bare generic, no console | P1 |
| 94 | pam/templates/pam_species_detailed.html:603 | map points | `r.ok`; console only | UI silent | P2 |
| 95 | pam/templates/pam_species_detailed.html:637 | time-scatter data | `r.ok`; bare generic | no console | P1 |
| 96 | pam/templates/pam_species_detailed.html:643,644 | barchart + weather (`Promise.all`) | **no `r.ok`** on either; console + generic | non-JSON -> SyntaxError (logged) | P2 |
| 97 | pam/templates/pam_species_detailed.html:660 | plot data | `r.ok`; bare generic | no console | P1 |
| 98 | pam/templates/pam_verification_interface.html:294 | filter cascade `$.ajax` | **no error handler** (only `complete`) | silent, filters stale | P1 |
| 99 | pam/templates/pam_verification_interface.html:570 | load next segment | 404 -> "no segments"; else `alert(responseJSON.error \|\| generic)` | non-JSON -> generic; alert; no console | P2 |
| 100 | pam/templates/pam_verification_interface.html:688 | W save verification | `responseJSON ? .error : generic` -> message box | no status, no console | P2 |
| 101 | pam/templates/pam_verification_interface.html:725 | user stats | console only | UI silent | P2 |
| 102 | pam/templates/pam_verification_segments.html:210 | segments table | `responseJSON ? .error : generic` | no status, no console | P2 |
| 103 | pam/templates/pam_verification_segments.html:242 | verifier rating | as #102 | as #102 | P2 |
| 104 | pam/templates/pam_verification_upload.html:229 | W verification archive upload (5 min timeout) | `JSON.parse(responseText)`; on parse failure "Помилка сервера. Перевірте логи." | 413 / 504 / CSRF HTML hidden behind a generic text (status not shown); no console | P2 |
| 105 | pam/templates/pam_yearly_table.html:149 | trends filters | no `r.ok`, **no catch** (only `.finally`) | unhandled rejection; `data.years` TypeError on error JSON | P1 |
| 106 | pam/templates/pam_yearly_table.html:173 | yearly table | `Promise.reject("Помилка сервера")` | bare generic, status dropped, no console | P1 |
| 107 | pam/templates/pam_yearly_trends.html:113 | yearly trends | `r.ok`; console + generic | server message ignored | P2 |
| **sdm** |||||
| 108 | sdm/templates/sdm/dashboard.html:198 | W enqueue job | `!r.ok` -> `HTTP <code>: <text[0:200]>`; alert | good content, alert only, no console | P3 |
| 109 | sdm/templates/sdm/dashboard.html:228 | job status poll (3 s `setInterval`) | `d.error -> return` keeps polling; `.catch(() => {})` | infinite silent polling, no stop/backoff (404 job polls forever) | P1 |
| 110 | sdm/templates/sdm/dashboard.html:248 | "show error" modal fetch (click) | no `r.ok`, **no catch** | click does nothing on failure | P1 |
| 111 | sdm/templates/sdm/map.html:139 | occupancy GeoJSON | no `r.ok`; `data.error` shown; catch `netError + e` | HTML -> "network error SyntaxError..."; no console | P2 |
| **templates/admin** |||||
| 112 | templates/admin/admin_institution_access.html:247 | load users | `if (!r.ok) throw r`; `message(T.failed)` | bare generic, status/body dropped, no console | P1 |
| 113 | templates/admin/admin_institution_access.html:269 | W save access | as #112; `T.saveFailed` | as #112 | P1 |
| **dead (excluded)** |||||
| - | camera_traps/templates/service_log.html:166,245,303,372 | old service log | `{_ g.lang_code _}` URLs are broken; route redirects away | delete template | - |
| - | pam/templates/pam_service_log.html:178,244,297 | old PAM service log | not rendered by any route | delete template | - |

## 3. Shared-helper plan

### Duplicated local helpers found (candidates to replace)

| Helper | Where | Notes |
|---|---|---|
| `describeAjaxError` / `logClientError` | camera_traps/templates/upload_fast.html:507-527 | the reference; jqXHR only |
| `errorText(xhr, fallback)` | camera_traps/templates/upload_video.html:342 | weaker clone of describeAjaxError |
| `isStaleResponse` / `readJson` | camera_traps/templates/identification.html:496-512 | the only login-redirect / `csrf_expired` handling |
| `apiGet` / `apiPost` | camera_traps/templates/admin.html:473-489 | fetch + json + throw-on-!ok |
| `getJSON` | camera_traps/templates/gap_analysis.html:429 and pam/templates/pam_gap_analysis.html:331 | byte-identical copies |
| run + status-poll block | camera_traps/templates/admin.html (x2: recalc :617-705, biotope :711-790) and pam/templates/pam_admin.html:100-150 | triplicated, all ignore the POST status |
| `showError(msg)` | CT admin:466, comparison:163, import_classification:302, species_dashboard:198; PAM pam_import:723 (+`showCreateError` :531), pam_species_dashboard:168, pam_yearly_trends:128; sdm/dashboard:247 | 9 page-local variants |
| `getErrorHtml` / `fail()` | pam_species_detailed, pam_cooccurrence:386, pam_segment_window | same idea again |
| `.then(r => r.json().then(d => ({ok: r.ok, d})))` | 10 sites | always breaks on non-JSON |
| CSRF token sourcing | meta tag (gallery, gap_analysis, sdm/dashboard, admin_institution_access), inline `'{{ csrf_token() }}'` (admin, manage_*, service_log), hidden `#csrf-field` (upload*, import_classification), form input (identification), `CSRF_TOKEN`/`CSRF` consts (pam_import, pam_sample_upload, pam_segment_window) | five different ways for one value |

### Base layout and CSRF

- Host layout: `app/templates/base.html`. It already emits `<meta name="csrf-token" content="{{ csrf_token() }}">` (line 7),
  has a global inline script outside `{% block scripts %}` (mobile nav toggle, lines 145-157) and then
  `{% block scripts %}{% endblock %}` (line 159). It does **not** load jQuery (each page does).
- Module layouts extend it: `camera_traps/templates/ct_base.html` (31 pages), `pam/templates/pam_base.html` (26 pages),
  `docs/templates/docs/_layout.html`; sdm and admin templates extend `base.html` directly. So one include in `base.html`
  reaches every page in scope.
- `camera_traps`, `pam`, `sdm`, `docs` are **git submodules shared with myproject** (`.gitmodules`). myproject has its own
  `app/templates/base.html` with the same csrf meta tag. Submodule templates must therefore not depend on a file that only
  biomon ships.

### Proposed location

- New file `app/static/js/http_errors.js` in **both host apps** (biomon and myproject, same content), served by the
  app-level `static` endpoint. Rejected alternative: putting it in `camera_traps/static/js` (served by
  `camera_traps.serve_ct_static`) would make PAM/SDM/admin depend on the CT submodule.
- Include it in each host's `base.html` right before `{% block scripts %}`, outside any block (same reason as the nav toggle:
  child templates that override `scripts` must not drop it), not `defer`, so inline page scripts can call it synchronously:
  ```
  <script>window.HTTP_I18N = {{ {'unknown': _('Невідома помилка'), 'noConn': _("Немає зв'язку з сервером (перевірте мережу)"), 'stale': _('Сесія застаріла, перезавантажте сторінку.')} | tojson }};</script>
  <script src="{{ url_for('static', filename='js/http_errors.js', v=1) }}"></script>
  ```
- Vanilla JS (no jQuery dependency), exposing `window.HttpErr`:
  - `csrf()` reads the meta tag (replaces the five token sources).
  - `fetchJSON(url, opts)` wraps fetch: `credentials: 'same-origin'`, `X-CSRFToken` on non-GET, `X-Requested-With`;
    checks Content-Type; detects login redirect (`response.redirected && /login/`) and `code === 'csrf_expired'` -> `err.stale`;
    throws `HttpError {status, message, body, url}` reading `error || message`, or a stripped 200-char snippet for HTML.
  - `describe(err, fallback)` accepts jqXHR, `HttpError`, `TypeError` from fetch (network -> `noConn`), plain `Error`;
    output format identical to `describeAjaxError` (`[HTTP 413] ...`).
  - `log(context, err, extra)` -> single `console.error('[biomon][<context>] HTTP=<status> ...')` line.
  - `poll(fn, {intervalMs, maxFails, stopOn})` with consecutive-failure limit (generalises upload_fast #52); fixes #3, #20, #60, #109.
  - `showStale()` one banner with a reload button, used whenever `err.stale`.
  - Optional: if `window.jQuery` exists at `DOMContentLoaded`, register `$(document).ajaxError` that only calls `log()` so
    every un-migrated `$.ajax` gets at least a console line.
- Keep `describeAjaxError`/`logClientError` names in upload_fast as thin aliases during migration.
- Rollout order (because of the shared submodules): ship `http_errors.js` + base include in biomon **and** myproject first,
  deploy both, only then migrate submodule templates.

### Pages to migrate (priority order)

1. P1 writes whose failure looks like success: `camera_traps/templates/admin.html` (#3, #5, #7), `pam/templates/pam_admin.html` (#71),
   `camera_traps/templates/gap_analysis.html` (#19-21), `sdm/templates/sdm/dashboard.html` (#109, #110),
   `templates/admin/admin_institution_access.html` (#112, #113), `camera_traps/templates/identification.html` (#26).
2. P1 silent loads on click / filter: `camera_traps/templates/manage_locations.html` (#34, #39), `pam/templates/manage_pam_locations.html` (#62, #63, #67),
   `camera_traps/templates/manage_deployments.html` (#30), `camera_traps/templates/ct_data_export.html` (#11), `pam/templates/pam_data_export.html` (#75),
   `pam/templates/pam_cooccurrence.html` (#72), `pam/templates/pam_verification_interface.html` (#98), `pam/templates/pam_yearly_table.html` (#105, #106),
   `pam/templates/pam_gap_analysis.html` (#78), `pam/templates/pam_sample_upload.html` (#85), `pam/templates/pam_species_detailed.html` (#93, #95, #97),
   `camera_traps/templates/activity_heatmap.html`, `behavior_analysis.html`, `daily_activity.html`, `species_detailed.html` (#1, #9, #13, #42).
3. P2 upload / import paths where 413/504/CSRF is realistic: `pam/templates/pam_import.html` (#80), `pam/templates/pam_verification_upload.html` (#104),
   `camera_traps/templates/upload.html` (#44-47), `camera_traps/templates/upload_video.html` (#54-60, replace `errorText` and `pollUntilDone`),
   `camera_traps/templates/import_classification.html` (#28, #29), `pam/static/js/segment_cutter.js` (#88), `camera_traps/templates/upload_fast.html` (#50: do not clear resume state on transient errors).
4. Remaining P2 CRUD and analytics: `manage_locations.html`, `manage_pam_locations.html`, `manage_deployments.html` (save/delete rows),
   `gallery.html`, `comparison.html`, `dashboard.html`, `species_dashboard.html`, `pam_overview.html`, `pam_species_dashboard.html`,
   `pam_yearly_trends.html`, `pam_evaluation_results.html`, `pam_verification_segments.html`, `pam_segment_window.html`, `sdm/map.html`.
5. Cleanup: delete `camera_traps/templates/service_log.html` and `pam/templates/pam_service_log.html`; then fold `identification.html`'s
   `readJson` into `HttpErr.fetchJSON` and turn `upload_fast.html` helpers into aliases.
6. Server-side companion (outside this client audit): JSON `401` for XHR / `/api/` in `unauthorized_handler`, and extend
   `handle_csrf_error` to XHR multipart requests (`X-Requested-With`), so the client helper receives JSON in those cases.
