# CT module server-side error-handling audit

Scope: `biomon/app/camera_traps/*.py` (34 files, ~21.2k lines; tests skipped). Read-only static review: AST scan of every `except` handler, `jsonify` error payload, `abort`, `flash` and logger call, plus manual reading of the important paths. Nothing was executed against a database.

Reference ("good") pattern: `create_batch` / `process_single_upload` / `finalize_batch` / `get_batch_status_api` / `finalize_batch_async` / `create_location` in `routes.py` (tag `[ct-upload]`, `logger.exception` on 5xx, `warning` with `user=`/`batch=` on 4xx, `[ExcName]` suffix on the client message).

## 1. Summary stats

### Except handlers (234 total)

| Pattern | Count | Notes |
|---|---|---|
| Handler logs something | 150 | |
| Logs **with** traceback (`logger.exception` or `exc_info=True`) | 74 | 26 `exception`, ~48 `error(..., exc_info=True)` |
| Logs **without** traceback (`error`/`warning` with `{e}` only) | 76 | about 30 of them are 5xx or user-facing failure paths |
| Neither logs nor re-raises | 78 | about 60 are legitimate parse/coerce fallbacks; **~15 are real problems** (see registry) |
| Body is only `pass` / `continue` | 25 | most are intentional best-effort; 5 are inner "the logger itself failed" guards |
| Bare `except:` | 1 | `daily_analytics.py:156` |
| Re-raise | 16 | |
| `print()` in a handler | 0 | but 3 `print()` calls in `service_analytics.py:99,104,116` |

### Client-facing errors

| Pattern | Count |
|---|---|
| `jsonify` payloads with `error`/`message` key | 208 (175 with `error`), all in `routes.py` (171) and `gap_routes.py` (4) |
| Error status codes used | 400×65, 500×52, 403×31, 404×20, 409×4, 410×1, 503×1, 507×1 |
| Error `jsonify` returned with HTTP 200 | 0 (two "degrade to 200" endpoints, see #22, #39) |
| 5xx carrying `[ExcName]` suffix | **6 of 52** (only the `[ct-upload]` group) |
| 5xx whose message is a bare generic English string (`'Server error'`, `'Error'`, `'Internal server error'`, `'Failed to load …'`) | 17 |
| 4xx returned outside an `except` with **no log line** | 93 of 99 (403×30, 404×18, 400×42, 409×2, 503×1) |
| Raw `str(e)` sent to the client | 13 `jsonify` sites + 2 `flash` sites + 2 DB columns shown in UI |
| `abort(...)` | 2 (`abort(404)`, both fine) |
| `flash(...)` | 56 (28 in `except`) |
| `flash(_(f'...'))` (f-string inside gettext: untranslatable, and 2 leak `str(e)`) | 4 |
| Route functions with **no top-level catch-all** (fall through to Flask's default HTML 500) | 18 in `routes.py`, 2 in `species_admin.py`; 5 JSON APIs among them |
| App-level `@errorhandler(500)` / `got_request_exception` hook | **none** (only `CSRFError` in `app/extensions.py:48`) |

### Logging

| Pattern | Count |
|---|---|
| Logger calls (`logger.*`, `current_app.logger.*`, `log.*`) | 237: error 98, info 66, warning 45, exception 26, debug 2 |
| With a greppable `[tag]` | 51 (~24%): `ct-upload` 21, `fast-upload` 8, `cleanup` 7, `ct-video` 5, `analytics` 4, `biotope-autoassign` 3, `gbif-cache` 2, `ct-manage` 1 |
| Carrying `user=` / current user context | ~40 |
| Untagged free-text error lines (`"Error in X: {e}"`) | ~150 |

**Bottom line.** The upload and video groups follow the reference pattern. Everything else lands in one of two buckets. The analytics, stats, manage and export pages log a full traceback but carry no tag or user, and the client sees a bare generic string. The identification, gallery, flag, admin tasks and service-log paths log one `error` line with no traceback and no context. The most serious finding is that the "good" upload endpoint is undermined from below (#1): `utils.process_single_photo` turns every failure into a `ValueError`, so real server errors are logged at DEBUG and returned as 400 with raw exception text.

## 2. Registry

Severity: **P1** means the user gets nothing useful and nothing is logged, or there is a silent data-loss risk, or internals leak. **P2** means it is logged, but without traceback or context, or the message is generic. **P3** is cosmetic or a consistency issue.

### P1

| # | file:line | endpoint / function | what happens | problem class | sev |
|---|---|---|---|---|---|
| 1 | utils.py:620-633 + routes.py:2724-2731 | `process_single_photo` → `/upload/process-single` | utils catches **every** exception (SQLAlchemy, OSError, the disk-full `IOError` from `_verify_files_on_disk`), logs `error` with no traceback and no user or batch, then re-raises as `ValueError(f"Failed to process file X: {str(e)}")`. The route treats `ValueError` as expected, logs it at **DEBUG** (invisible in prod) and returns **400 with raw `str(e)`**. The route's `logger.exception` + `[ExcName]` 500 path is effectively dead for photo processing. The 2026-07-08 disk-full class of incident would show up as 400s with no WARNING/ERROR under `[ct-upload]`. Inverse noise: ordinary duplicates are logged at ERROR. | leak (SQL / paths in `str(e)`) + wrong status (500→400) + traceback lost + wrong level | P1 |
| 2 | routes.py:5066-5067 | `/upload/process` (legacy `process_upload`) → `process_photo_batch` | the same wrapped `ValueError` is returned as 400 with `str(e)` and is **not logged** at the route | leak + unlogged + wrong status | P1 |
| 3 | utils.py:721-723 | `group_batch_into_series` (live via `/api/finalize-batch`, routes.py:2755) | a per-photo `except Exception: log error; continue` leaves the photo ungrouped while the batch is still marked `completed` and the user sees success. After a failed `flush()` the session is unusable, so the error cascades or is masked. No traceback. | silent data-loss risk (orphan photos, success reported) | P1 |
| 4 | utils.py:747-751 | `group_batch_into_series` outer except | sets `batch.status='failed'` and `batch.error_message=str(e)`, then calls `ct_session.rollback()`, which **discards both**. The batch stays in its old status with no recorded failure. Log is `error` without traceback. | silent loss of failure state | P1 |
| 5 | daily_analytics.py:156 | `process_sample_kde` (daily-activity API) | a bare `except:` returns `np.zeros_like(x_grid)`. A failed KDE (singular data, bad bw) becomes a flat-zero curve that is averaged into bootstrap CIs and drawn as "no activity". Nothing is logged, and it also swallows `KeyboardInterrupt`/`SystemExit`. | silent swallow that corrupts scientific output | P1 |
| 6 | background_tasks.py:229-234 → routes.py:5090 | `cleanup_old_photos` → `/admin/run-cleanup` | returns `{'error': str(e)}`, and the route flashes `_(f'Помилка очищення: {result["error"]}')`. Raw DB/OS text reaches the UI (admin only). Log has no traceback. | leak + no traceback + f-string in gettext | P1 |
| 7 | background_tasks.py:503-505 → routes.py:6751 | `delete_unfavorited_raw_files` → `/admin/delete-unfavorited-originals` | same pattern: `str(e)` is flashed to an admin after a destructive operation, and the log has no traceback | leak + no traceback | P1 |
| 8 | routes.py:4625-4962 | `/api/next-observation-for-identification` (core identify flow) | `try/finally` with no `except`. Any DB error gives Flask's default **HTML** 500, so the `fetch` client fails parsing JSON and the identifier sees a generic JS error. Flask logs a traceback, but with no tag, user or obs context. | 5xx without JSON message in a user-facing API | P1 |
| 9 | routes.py:3208-3247 | `/api/video/sign-clip` | catches only `VideoUploadError`/`ValueError` and returns them as 400 `str(e)`, **unlogged**. Any other exception gives an HTML 500. `float(step_seconds)` errors reach the user as Python internals ("could not convert string to float"). | unlogged 4xx + no catch-all | P1 |
| 10 | ai_runner.py:200, 229 + routes.py:174-178 | `is_ai_paused`, `get_ai_pause_status` → `/admin/ai/pause-status` | any DB error returns `paused=False` **without logging**. The admin indicator shows "not paused" (not "unknown", contrary to the docstring) while classification state is in fact unknown. | silent swallow, misleading state | P1 |

### P2: logged, but without traceback, context or a specific message

| # | file:line | endpoint / function | what happens | problem class | sev |
|---|---|---|---|---|---|
| 11 | routes.py:4474-4477 | `/api/submit-identification` | `logger.error("Error saving series identification: {e}")` gives no traceback, no obs id and no user. The client gets `'Помилка бази даних.'` even for non-DB bugs (e.g. consensus code). | no tb / context; misleading generic msg | P2 |
| 12 | routes.py:4377 | `/api/submit-identification` (payload parse) | 400 `'Неправильний формат даних.'` with no log and no indication of which field failed | unlogged 4xx, generic | P2 |
| 13 | routes.py:192-198, 218-224 | `flag_observation`, `unflag_observation` | `error` without traceback, generic flash | no tb, no user | P2 |
| 14 | routes.py:2984-2987 | `/api/batch/<id>/uploaded-files` | `error` without traceback or tag, generic 500 with no `[ExcName]` (sits inside the `[ct-upload]` group) | inconsistent with reference | P2 |
| 15 | routes.py:4521-4524 | `/api/location/<id>` (`get_location_details`) | `error` without traceback, generic 500 | no tb | P2 |
| 16 | routes.py:5068-5070 | `/upload/process` generic branch | `error` without traceback; client gets `'Сталася невідома помилка…'` | no tb, "unknown" msg | P2 |
| 17 | routes.py:5091-5093, 6752-6754 | `manual_cleanup`, `manual_delete_originals` outer except | `error` without traceback; flash says "check the logs", but the logs hold no traceback | no tb | P2 |
| 18 | routes.py:5229-5232 | `/admin/recalculate-consensus` | same: "check the logs" while the log has no traceback | no tb | P2 |
| 19 | routes.py:5267-5271 | `/api/review-filters` | `error` without traceback; 500 `'Failed to load filters'` | no tb, generic EN | P2 |
| 20 | routes.py:5430-5434, 5552-5556, 5594-5599 | `gallery`, `/api/gallery/photos`, `/api/gallery/remove-favorite` | `error` without traceback; `'Server error'` (EN, untranslated) | no tb, bare generic | P2 |
| 21 | routes.py:5838-5845 | `/location/<id>/validity` | `error` without traceback, generic | no tb | P2 |
| 22 | routes.py:6595-6598 | `/api/update-location/<id>` | `error` without traceback; `'Помилка збереження даних.'` | no tb, no user | P2 |
| 23 | routes.py:7217-7221 | `/api/identification-stats` | `error` without traceback; `'Failed to load statistics'` | no tb, generic EN | P2 |
| 24 | routes.py:565-569, 793-797, 1011-1014, 1521-1525, 1727-1731 | `dashboard`, `contributors`, `institution_stats`, `expertise`, `upload_stats` | `error(f"Error in X: {str(e)}")` without traceback; page renders empty with a generic warning flash | no tb; silent empty page | P2 |
| 25 | routes.py:2244-2247, 2295-2298, 7688-7691 | `get_species_ranking`, `get_behavior_ranking`, `get_cached_species_for_filter` | log `error` without traceback, return `{}`/`[]`; filters and rankings silently empty | swallow to default | P2 |
| 26 | routes.py:3736-3741, 3829-3833, 3897-3901, 4038-4042, 4301-4305, 2099-2102, 7014-7018, 7333-7337, 7389-7393, 7889-7893, 8040-8044, 8315-8319; gap_routes.py:175-177, 251-253 | stats/analysis/service-log JSON APIs | traceback **is** logged (`exc_info=True`), but with no tag, user or params; client gets `'Error'`, `'Server error'`, `'Internal server error'`, `'Internal error'`, `'Failed to load …'` (EN, no `[ExcName]`). `stats_locations` returns literally `'Error'`. | bare generic message, no context | P2 |
| 27 | routes.py:6111, 6149, 6174, 6665, 6728, 7474, 7546, 7566 | deployment / location / service-visit / stats write APIs | traceback logged, no tag or user; `'Помилка сервера.'`, `'Помилка збереження даних.'` with no `[ExcName]` | generic, no context | P2 |
| 28 | routes.py:3397, 3432, 3461, 3497 | import-classification / verification preview & run | `logger.exception` (good), but no tag/user/location/filename; generic 500 with no `[ExcName]` | no context | P2 |
| 29 | routes.py:5173, 5201, 5217, 6828, 6909 | cleanup / analytics / biotope status & start | `logger.exception` (good), no tag (the `cleanup.py` module itself uses `[cleanup]`), generic 500 | no tag/context | P2 |
| 30 | routes.py:5193-5200 | `/admin/cleanup/execute/<id>` ValueError | maps status by **substring of the English exception text** (`'not found'`, `'expired'`), unlogged, and returns the raw untranslated message for a destructive admin action | fragile + unlogged + leak of internal text | P2 |
| 31 | routes.py:7120, 8171 | `/api/data-download`, `/api/stats/daily-activity/download` | traceback logged; client gets plain text `"Export error"` / a generic string | generic, no context | P2 |
| 32 | routes.py:6074 `api_get_deployment`; 2108 `api_behavior_species`; 2392 `identify_ai_species_list` | JSON APIs with no top-level catch-all | DB error gives an HTML 500; Flask's default log line carries no context | 5xx not JSON | P2 |
| 33 | species_admin.py:174-198 | `/admin/species/new`, `/admin/species/<id>` POST | no try; an `IntegrityError` (duplicate scientific name, etc.) from `save_species` gives an HTML 500, while the admin expects a form error | wrong status, no message | P2 |
| 34 | routes.py:2306, 2462, 2811, 3020, 3337, 5606, 7697, 234 | `identify`, `upload`, `upload_fast`, `upload_video`, `import_classification`, `ct_location_coverage`, `species_detailed`, `admin_flagged_list` HTML pages | no catch-all; a DB error gives a bare Flask 500 page with no flash | no message (logged by Flask) | P2 |
| 35 | fast_upload.py:368, 373 (+ utils.py:773) | `_run_grouping_in_thread` → `batch.error_message` | `str(e)` (truncated to 500 chars) is stored and **rendered** in `upload_fast.html:1161` and `admin.html:568,645,737,741`. Traceback is logged (good), but raw SQL can reach the uploader's UI. | leak (manager/admin) | P2 |
| 36 | background_tasks.py:210, 488; utils.py:722 | per-item loops in archival / deletion / grouping | `error` per item with no traceback, then `continue`; the summary result does not count failures | no tb, partial-failure not surfaced | P2 |
| 37 | background_tasks.py:293; utils.py:777 | `get_batch_statistics`, `get_batch_status` | log `error` without traceback, return `{}` / `None`; the caller reports "not found" for a DB outage | swallow to default (wrong 404) | P2 |
| 38 | utils.py:304 | `extract_datetime_from_exif` | `error` "Could not read EXIF data…" with no filename and no batch; at ERROR level for an expected condition | missing context, wrong level | P2 |
| 39 | gap_routes.py:238 | `/api/gap-analysis/gbif/status` | exception gives 200 `{'status': 'unavailable'}` (traceback logged). Acceptable degradation, but the client cannot tell an outage from "not built yet". | error as 200 | P2 |
| 40 | ai_runner.py:58, 127, 184 | AI schema / lease set / lease clear | `warning` without traceback or tag | no tb/tag | P2 |
| 41 | notifications.py:79 (and :74) | `send_identification_reminders` | `error` without traceback; logs `user.email` (PII in logs) | no tb + PII | P2 |
| 42 | database.py:69 | `init_ct_database` | `error` without traceback before `raise` (the re-raise keeps it, but duplicates the line) | no tb | P3 |

### P3: cosmetic / consistency

| # | file:line | endpoint / function | what happens | problem class | sev |
|---|---|---|---|---|---|
| 43 | routes.py (≈93 sites, e.g. 2569, 2573, 4386, 4393, 6079-6081, 6096-6098, 6132-6134, 6166-6168, 6540-6576, 6702-6708, 7352-7360, 7418-7426, 7490-7509, 5811-5818, 4647) | 403/404/409 guards | returned without any log line; only `_location_manage_error` (routes.py:2587) logs `[ct-manage] refused`. A 403 spike is invisible. | unlogged 4xx | P3 (403s: borderline P2) |
| 44 | routes.py:3769, 4060, 1938, 3939, 4071, 4087-4089, 4118, 5452, 5572, 5576, 7758, 7923, 7926, 8246, 8291, 3950/3957, 7939/7946, 8257/8264; gap_routes.py:201, 248 | analysis APIs | English, untranslated error strings (`'Access denied'`, `'Missing parameters'`, `'Invalid date'`, `'Forbidden'`), unlogged | i18n / consistency | P3 |
| 45 | routes.py:5086, 5090, 5230, 6751 | admin flashes | `flash(_(f'…{x}…'))`: the f-string is evaluated before gettext, so it can never be translated (use `%(n)s` params) | i18n bug | P3 |
| 46 | routes.py:415, 1945, 7919, 8068, 8237, 8242; data_export.py:355 | date / bw / heatmap param parsing | bad input silently replaced by a default and not logged; the user is not told their filter was ignored | silent default | P3 |
| 47 | analytics_calculator.py:573, 667; biotope_autoassign.py:504; gbif_cache.py:533; cleanup.py:610; fast_upload.py:443 | thread runners / recovery | inner `except Exception: pass` around the "mark failed" / logger call. The primary error is already logged; the secondary failure (status row stuck `running`) is not. | silent secondary swallow | P3 |
| 48 | analytics_calculator.py:664; cleanup.py:607; fast_upload.py:437 | `recover_stuck_*` | `error` with `{e}` and no traceback | no tb | P3 |
| 49 | utils.py:631 | `process_single_photo` cleanup of partial files | `os.remove` failure swallowed: an orphan file remains and is not logged | silent swallow | P3 |
| 50 | utils.py:508 | advisory lock | `except Exception: pass` (intended for SQLite). On Postgres a real failure silently disables the duplicate-race guard. Should log at least once. | silent swallow | P3 |
| 51 | ai_runner.py:150 | `heartbeat_ai_pause` | silent `pass` (documented; the TTL is the backstop) | intentional | P3 |
| 52 | routes.py:1558 | `_is_postgres` | silent `False`; on prod would switch to SQLite SQL branches | silent default | P3 |
| 53 | database.py:81 | `test_ct_connection` | returns `str(e)` with no log (apparently unused in CT; sdm has its own) | dead code / leak-prone | P3 |
| 54 | service_analytics.py:99, 104, 116 | `update_all_location_stats` | `print()` instead of logger (lost under gunicorn) | print | P3 |
| 55 | routes.py:2559-2575 | `_batch_access_error` | 404/403/400 with no log, unlike `_location_manage_error`; `int(location_id)` sits outside any except, so a `ValueError` surfaces as 400 with Python text via the caller | inconsistency | P3 |
| 56 | routes.py:118, 128, 145, 100, 321, 2367, 2450, 4705, 4954 | admin panel / AI widgets | `warning` without traceback, page degrades silently (acceptable best-effort) | no tb | P3 |
| 57 | cleanup.py:132, 535, 576; background_tasks.py:163, 330 | per-file rm / purge | `warning` with path and `{e}` (OK). Only the tag is missing in background_tasks. | tag | P3 |
| 58 | all `{'error': …}` responses | response shape | about half include `'success': False` and half do not; only `api_delete_location` sends a machine `code` | consistency | P3 |

Legitimate, not flagged: parse/coerce fallbacks in `classification_import.py`, `deployment_import.py` (errors are collected into the report), `video_timestamp.py`, `video_upload.py`, `video_metadata.py`, `gbif_cache.py:123/229/303/360`, `cleanup.py:251/303/568`, `background_tasks.py:418`, `utils.py:345`, `ai_runner.py:725`, `gap_routes.py:64`, `notifications.py:22` (import fallback).

## 3. Proposed log tags (from the actual blueprint structure)

There is one blueprint (`camera_traps`, prefix `/<lang_code>/camera-traps`) spread across `routes.py`, `gap_routes.py`, `species_admin.py` and the helper modules. The tags below follow URL groups and keep the existing ones.

| Tag | Covers (routes / modules) | Existing |
|---|---|---|
| `[ct-upload]` | `/upload*`, `/api/create-batch`, `/api/batch*`, `/api/finalize-batch*`, `/api/create-location`; `utils.process_single_photo`, `utils.group_batch_into_series`, `create_upload_batch` | yes (extend into `utils.py`) |
| `[ct-grouping]` (or keep `[fast-upload]`) | `fast_upload.py` background grouping, `recover_stale_grouping_batches` | `[fast-upload]` |
| `[ct-video]` | `/upload-video`, `/api/video/*`; `video_upload.py`, `video_timestamp.py`, `video_metadata.py` | yes |
| `[ct-identify]` | `/identify`, `/api/next-observation-for-identification`, `/api/submit-identification`, `/api/identify/ai-species`, `/api/identification-stats`, `/observation/<id>/flag|unflag`, `/admin/flagged`; `consensus.py`, `utils.check_consensus_*`, `reopen_observation`, `mark_observation_complete` | no |
| `[ct-ai]` | `/admin/ai/*`, `ai_runner.py` (pause lease, run requests, predictions) | no (`"AI: …"` prefix today) |
| `[ct-gallery]` | `/gallery`, `/api/gallery/*`, `/photo/<id>`, `/observation/<id>/photo/<i>`, `/thumbnails/*`, `/photos/raw/*` | no |
| `[ct-manage]` | `/manage-locations`, `/api/location/*`, `/api/update-location/*`, `/location/<id>/validity|coverage`, `/manage-deployments`, `/api/deployment*`, `/api/update-deployment/*`, `/data-quality`; access helpers `_location_manage_error`, `_batch_access_error` | yes (1 site) |
| `[ct-service]` | `/service-log`, `/api/service-log/create`, `/api/service-visit/*`, `/api/locations-with-status`, `/api/location/<id>/service-history` | no |
| `[ct-import]` | `/import-classification*` (classification + verification); `classification_import.py`, `verification_import.py`, `deployment_import.py` | no |
| `[ct-export]` | `/data-export`, `/api/data-preview`, `/api/data-download`, `/api/get-taxonomic-filters`, `/export-deployments`, `/api/stats/daily-activity/download`; `data_export.py` | no |
| `[ct-stats]` | `/dashboard`, `/contributors`, `/institution-stats`, `/expertise`, `/upload-stats`, `/api/stats/top-*`, `/api/stats/locations`, `/api/review-filters`, `/api/run-stats-calculation`; `service_analytics.py` | no |
| `[ct-analysis]` | `/analysis/*` (species dashboard, comparison, behavior, daily-activity, activity-heatmap, species-detailed), `/api/stats/species-dynamics|comparison|distribution-map|daily-activity|activity-heatmap`, `/api/behavior/*`; `daily_analytics.py`, `activity_heatmap.py` | no |
| `[ct-gap]` | `gap_routes.py` (`/analysis/gap-analysis`, `/api/gap-analysis/*`), `gap_analysis.py`, `gap_metrics.py` | no |
| `[gbif-cache]` | `gbif_cache.py` | yes |
| `[ct-analytics]` | `/admin/run-analytics`, `/admin/analytics/status`; `analytics_calculator.py` | `[analytics]` (rename or keep) |
| `[ct-biotope]` | `/admin/biotopes/auto-assign*`; `biotope_autoassign.py` | `[biotope-autoassign]` |
| `[ct-cleanup]` | `/admin/cleanup/*`, `/admin/run-cleanup`, `/admin/delete-unfavorited-originals`; `cleanup.py`, `background_tasks.py` | `[cleanup]` |
| `[ct-admin]` | `/admin` panel, `/admin/recalculate-consensus`, `/admin/species*` (`species_admin.py`) | no |
| `[ct-notify]` | `notifications.py` | no |

Cross-cutting recommendations (to make the tags pay off):

1. Add a blueprint-level `@camera_traps_bp.errorhandler(Exception)`, or better an app-level one. It should log `logger.exception("[ct-unhandled] {method} {path} user=…")` and return JSON `{'error': …, '[ExcName]'}` for `/api/*` or XHR, and a flash plus redirect otherwise. This closes #8, #9, #32, #33 and #34 in one place.
2. Stop wrapping in `utils.process_single_photo`. Let only real validation raise `ValueError` (or a dedicated `UploadRejected`) and let everything else propagate unwrapped, so the route's 500 path works (#1, #2).
3. Small helper `_ct_fail(tag, msg, status=500, **ctx)` doing `logger.exception` + `jsonify({'error': msg + f' [{type(e).__name__}]'})`, applied to the 52 5xx sites. Plus `_ct_reject(tag, msg, status, **ctx)` doing `logger.warning` for 4xx, at least for 403s.
4. Never put `str(e)` into `flash`, `jsonify` or DB `error_message` columns shown in the UI. Show `[ExcName]` plus a timestamp or request id instead, and keep the detail in the log.
