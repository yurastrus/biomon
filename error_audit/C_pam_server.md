# PAM module: server-side error-handling audit

Scope: `biomon/app/pam/*.py` (tests excluded), read-only, 2026-11-04. Nothing was executed against any database.
Reference: `app/camera_traps/routes.py` upload-fast endpoints (`[ct-upload]` tag, `logger.exception` on 5xx, `warning/info` with `user=`/`batch=` on 4xx, `[ExcName]` appended to the client message).

Method: AST scan of every `except` handler (194 handlers, with enclosing function and route), plus grep of `jsonify(... 'error')`, `abort`, `flash`, `raise`, `logger.*`, `print(`, followed by manual reading of every non-trivial handler. Helper scripts: `scan.py`, `routes_notry.py`; raw handler dump: `excepts.tsv` (same scratchpad folder).

---

## 1. Summary stats

| Metric | Count |
|---|---|
| Route functions (routes.py + gap_routes.py + species_admin.py) | 85 |
| `except` handlers total (all PAM .py) | 194 |
| ... of which `except Exception` | 138 |
| ... bare `except:` | 3 (pam_evaluation_utils.py:692, 709, 739) |
| ... body is only `pass` / `continue` | 5 / 3 |
| ... no logging and no re-raise at all | 62 (about 25 are legitimate query-param fallbacks, about 15 are 4xx returns, about 20 are real silent swallows, see 2a) |
| Handlers that keep the traceback: `logger.exception` | **2** (routes.py:264, biotope_autoassign.py:518) |
| Handlers that keep the traceback: `exc_info=True` | 46 (8 of them use the root `logging` module in pam_analytics_calculator.py) |
| Handlers that keep the traceback: `traceback.format_exc()` as a separate `logger.error` line | 21 (traceback is a second, untagged log record, interleaves under gunicorn workers) |
| Handlers that log at error/warning **without** any traceback | 66 |
| `logger.error(` calls / `logger.warning(` / `logger.info(` | 158 / 12 / 100 |
| Greppable tag `[pam-...]` | 3 lines, all in biotope_autoassign.py |
| Log lines that carry `current_user` id/username | 8 (none of them on an error path; only info "started by ...") |
| Client message with exception class `[ExcName]` | 0 |
| 5xx responses in routes (`jsonify(...), 500`, `abort(500)`, `"...", 500`) | 53 |
| ... with a bare generic body (`'Internal server error'`, `'Server error'`, `'Export failed'`, `'Помилка сервера.'`) | 22 |
| User-facing 5xx handlers that log **no traceback** | 18 (list in 2c) |
| `print(` instead of logger | 4 (routes.py:2595, 2605; utils.py:1464, 1534) |
| `abort(404/403)` raised inside `try/except Exception` and therefore turned into 500 | 5 sites in 2 endpoints |
| Route functions with no `except` (fall through to Flask default HTML 500) | 9 (`pam_home`, `pam_overview`, `segment_window`, `species_evaluation_detail`, `manage_pam_locations`, `update_pam_location`, `pam_service_plan`, `species_admin_list`, `species_admin_edit`) |

No app-level `@errorhandler(500)` exists (only CSRF in `app/extensions.py:48`), so unhandled exceptions are logged by Flask's default "Exception on ... [GET]" record (traceback kept, but no tag, no user, HTML body to JSON callers).

### Pattern overview

| # | Pattern | Count | Typical severity |
|---|---|---|---|
| A | DB/IO error converted to an empty/default value (`return []`, `{}`, `None`, `0`) in data helpers; route returns **200 with empty data**, user sees "no detections" instead of an error | 24 helpers | P1/P2 |
| B | Silent swallow (no log at all) that drops data or hides a failure | 13 | P1 |
| C | 5xx in a user-facing endpoint logged without traceback | 18 | P2 |
| D | Traceback split into a second `logger.error(traceback.format_exc())` record | 21 | P3 |
| E | Raw `str(e)` of DB/OS/library exceptions shown to the user (JSON or flash or upload report) | 7 | P1 (non-admin) / P2 (admin) |
| F | HTTPException (`abort(404)`, `abort(403)`, `request.json` 415/400) caught by `except Exception` and re-emitted as 500 | 3 endpoints | P1 |
| G | Bare generic client message, no exception class, no hint | 22 bodies + about 20 flash texts | P2 |
| H | 4xx validation rejections not logged (no user, no params) | about 45 returns | P3 (P2 for access-denied 403) |
| I | User-input errors logged at ERROR level (noise) or 4xx emitted for server faults | 7 | P3 / P1 for disk-full-as-400 |
| J | `print()` in request/background code | 4 | P2 |
| K | Batch/background job swallows errors and exits 0 / leaves state stuck | 4 | P1/P2 |
| L | No greppable tag and no context (user=, ids) on any error line | module-wide | P2 |

---

## 2. Registry

Severity key: **P1** = user sees nothing useful AND nothing logged / silent data-loss or wrong-data risk / internal-info leak; **P2** = logged but without traceback or context, or generic message; **P3** = cosmetic/consistency.

### 2a. Silent swallows and data-loss risk (exhaustive)

| # | file:line | endpoint/function | what happens | problem class | sev |
|---|---|---|---|---|---|
| 1 | pam_import_utils.py:386 | `PAMImportProcessor.process_batch` (POST /api/pam/import) | Any exception while reading/parsing one uploaded CSV: `files_failed += 1`, nothing logged, no filename, no reason. This also swallows the **intentional** `ValueError("Cannot find required column ...")` from parse_csv:138/238, so the user only sees "failed: 1" | B silent swallow, data loss | P1 |
| 2 | pam_import_utils.py:185 | `BirdNETImporter.parse_csv` | Malformed row (`ValueError/TypeError/KeyError`) → `continue`; row not counted in stats, not logged. Detections disappear without trace | B silent row drop | P1 |
| 3 | pam_import_utils.py:280 | `RavenImporter.parse_csv` | Same as #2 for Raven tables | B silent row drop | P1 |
| 4 | pam_import_utils.py:57 | `_parse_datetime_from_filename` | Bad date in filename → `None`; `_process_recording` (476) inserts recording with `datetime_start = NULL`; not counted, not logged | B silent data-quality loss | P2 |
| 5 | pam_upload_utils.py:279 | `process_species_folder` (ZIP upload) | Whole species folder fails → logs error without traceback, `return False`; return value ignored at 448, **nothing added to `stats['error_files']`**, user gets "success" with fewer files | B + C | P1 |
| 6 | pam_upload_utils.py:453 | `process_zip_archive` loop | Same as #5 one level up; species skipped, not reported to user | B + C | P1 |
| 7 | pam_upload_utils.py:163 | `ensure_system_user` | DB error → `return 0` anyway; every later insert then fails on FK to user 0 and is reported per-file with raw SQL text (see 2d #1) | A masking root cause | P2 |
| 8 | biotope_autoassign.py:521 | `_run_autoassign_in_thread` | If writing the 'failed' status itself fails → `except Exception: pass`; DB row can remain `running` forever, admin page polls "running" | B silent, stuck state | P1 |
| 9 | biotope_autoassign.py:178 | `gee_landcover_available` | `import ee` failure swallowed; admin sees "GEE недоступний" without knowing if lib or key is missing | B | P3 |
| 10 | pam_analytics_calculator.py:464 | `_get_all_institution_ids` | Any DB error → `[]`; per-institution trends silently not computed, run reported as successful | B, wrong data persisted | P1 |
| 11 | pam_analytics_calculator.py:183 | `_precalculate_biotope_effort_map` | Logs (no traceback) and returns an empty effort frame; biotope activity then computed with no effort, written to DB | A, wrong data persisted | P1 |
| 12 | pam_analytics_calculator.py:122 | `_get_species_confidence_thresholds` | Falls back to default threshold for ALL species (logged with exc_info + warning) and continues; analytics silently change meaning | A | P2 |
| 13 | pam_analytics_calculator.py:602, 604, 629 | `run_pam_analytics` / `__main__` | All exceptions logged then swallowed; process **exits 0**, cron/systemd cannot see failure; also `logging.basicConfig` at import (line 13) | K | P2 |
| 14 | pam_evaluation_utils.py:692 | `calculate_logistic_regression.get_thresholds_from_model` | bare `except: return None` → result marked `'error'` with no log | B, bare except | P2 |
| 15 | pam_evaluation_utils.py:709 | `calculate_logistic_regression` | bare `except:` → pseudo-R² = 0.0 silently | B, bare except | P3 |
| 16 | pam_evaluation_utils.py:739 | bootstrap loop | bare `except: continue`; CI computed from fewer iterations, count not reported | B, bare except | P3 |
| 17 | pam_evaluation_utils.py:575-606 | `cleanup_completed_verifications` (POST /admin/verification/cleanup, role manager) | File is `os.remove`d **before** the `UPDATE ... 'archived'`; a DB error after delete is caught per segment, Postgres tx becomes aborted, later updates fail, final commit may fail → files gone, status still `completed`. UI shows only "Помилки: N" | data-loss risk, B | P1 |
| 18 | pam_segment_sampling.py:693 | `_quiet_remove` | `OSError: pass`; orphan FLAC left on disk unlogged | B | P2 |
| 19 | pam_upload_utils.py:300-325 | `process_audio_files_in_folder` | `shutil.copy2` happens inside `conn.begin()` before DB insert; when the insert fails the copied file stays as an orphan (error is reported, file is not cleaned) | data hygiene | P2 |
| 20 | routes.py:2382 | `admin_recalculate_metrics` | Garbage `species_choice` → `pass` → silently recalculates **all** species (heavy job) | B, wrong action | P2 |
| 21 | routes.py:2862 | `get_pam_location_details` (GET /pam/api/location/<id>) | `except Exception: return 500` with **no log at all** | B | P1 |
| 22 | routes.py:3010 | `api_create_pam_location` | rollback failure `pass` (primary error logged) | B | P3 |
| 23 | routes.py:4529 | `api_get_weather_overlay` | Centroid query fails → logged (no traceback), silently uses DEFAULT coordinates → weather of a wrong place shown | A | P2 |
| 24 | utils.py:2020 | `get_weather_data` | Open-Meteo failure logged (no traceback); cache-only/empty returned, user not told | A | P3 |
| 25 | pam_upload_utils.py:435 | `process_zip_archive` | file-count failure → `'N/A'` | B | P3 |

### 2b. Error masked as empty result (pattern A, 200 with empty data)

All log at ERROR, most without traceback, none with tag/user/params; caller route never reaches its own `except`, so the client gets HTTP 200 and an empty chart/table ("Немає даних").

| # | file:line | function → affected endpoint(s) | traceback? | sev |
|---|---|---|---|---|
| 26 | utils.py:305 | `get_available_species` → dashboard pages | no | P2 |
| 27 | utils.py:377 | `get_model_conf_columns` → all model-aware queries (returns stale/empty cache) | no | P2 |
| 28 | utils.py:542 | `get_models_list` → import, sample, gap, segment-window pages (empty model dropdown) | no | P2 |
| 29 | utils.py:570 | `get_reference_model_id` → `None` | no | P2 |
| 30 | utils.py:645 | `get_filtered_detections` → /api/pam/get-plot-data | no | P1 (user told "no detections") |
| 31 | utils.py:725 | `get_daily_detection_counts` → /api/pam/get-barchart-data | no | P1 |
| 32 | utils.py:841 | `get_time_scatter_data` → /api/pam/get-time-scatter-data | yes | P2 |
| 33 | utils.py:983 | `get_species_summary` → /api/pam/get-species-summary | yes | P2 |
| 34 | utils.py:1074 | `get_unique_detection_points` → /api/pam/get-unique-points | no | P1 |
| 35 | utils.py:1193 | `get_species_ranking` → /api/pam/get-species-ranking | yes | P2 |
| 36 | utils.py:1333 | `get_overview_statistics` → /api/pam/get-overview-stats (zeros shown as real numbers) | no | P1 |
| 37 | utils.py:1440 | `get_locations_for_map` → /api/pam/get-locations-map | no | P1 |
| 38 | utils.py:2082 | `get_weather_data` → /api/pam/get-weather-overlay | yes | P2 |
| 39 | utils.py:255 | `calculate_sun_times_simple` → fixed fallback sunrise/sunset shown as real | no | P2 |
| 40 | pam_evaluation_utils.py:62 | `get_species_for_dropdown` | no | P2 |
| 41 | pam_evaluation_utils.py:245 | `calculate_species_metrics` → `None`, species counted as "skipped" in recalculation | no | P2 |
| 42 | pam_evaluation_utils.py:282 | `find_optimal_threshold` → 0.5 (currently unused, dead code) | no | P3 |
| 43 | pam_evaluation_utils.py:531 | `get_evaluation_summary` → zeros page | format_exc | P2 |
| 44 | pam_evaluation_utils.py:787 | `calculate_logistic_regression` | no | P2 |
| 45 | pam_evaluation_utils.py:991 | `get_species_logistic_data` → /pam/evaluation/species/<id> says "дані відсутні або ще не розраховані" on DB failure | no | P1 (misleading) |
| 46 | pam_upload_utils.py:517 | `get_upload_statistics` → upload page with empty stats | no | P2 |
| 47 | pam_segment_sampling.py:502 | `get_earliest_recording_year` | no | P3 |
| 48 | pam_linking_utils.py:118 | `link_verifications_to_detections` batch skipped, continues | format_exc | P2 |
| 49 | pam_segment_sampling.py:675 | `save_and_register_segment` → route answers generic "Failed to save segment" 500 | no | P2 |

### 2c. User-facing 5xx without traceback (exhaustive)

| # | file:line | endpoint | what happens | class | sev |
|---|---|---|---|---|---|
| 50 | routes.py:1909 | POST /api/verification/submit | `logger.error(f"Error submitting verification: {e}")` + raw request body; no traceback, no user id; client "Помилка збереження верифікації". Core verifier flow | C, G, L | P2 |
| 51 | routes.py:2040 | GET /api/verification/stats | error line only | C, G | P2 |
| 52 | routes.py:2533 | GET /api/verification/consensus-status | error line only | C, G | P2 |
| 53 | routes.py:3202 | GET /api/pam/verification/top-verifiers | error line only | C, G | P2 |
| 54 | routes.py:1293 | GET /api/pam/get-filters-data | error line only, "Failed to load filter data" | C, G | P2 |
| 55 | routes.py:2704 | GET /api/pam/evaluation/thresholds | error line only | C, G | P2 |
| 56 | routes.py:2223 | GET /audio/segments/<id> | `abort(500)` after error line; also swallows 404/403 (see 2e) | C, F | P1 |
| 57 | routes.py:2582 | GET /audio/spectrograms/<id> | same as #56 | C, F | P1 |
| 58 | routes.py:2862 | GET /pam/api/location/<id> | no log at all | B | P1 |
| 59 | routes.py:3589 | GET /api/pam/data-preview | error line only | C | P2 |
| 60 | routes.py:3634 | GET /api/pam/data-download | error line only, plain-text 500 | C | P2 |
| 61 | routes.py:4787 | GET /pam/evaluation/export-species/<id> | "Export failed", 500, no traceback | C, G | P2 |
| 62 | routes.py:392 | POST /pam/verification/upload/process | `makedirs` OSError logged without traceback | C | P2 |
| 63 | routes.py:332 | GET /pam/verification/upload | template-fallback failure logged without traceback | C | P3 |
| 64 | routes.py:2430 | POST /admin/evaluation/recalculate | flash generic, log without traceback | C, G | P2 |
| 65 | routes.py:2462 | POST /admin/verification/cleanup | flash generic (typo "Неспідівана"), no traceback | C, G | P2 |
| 66 | routes.py:3125 | POST /admin/evaluation/convert-to-flac | same, same typo | C, G | P2 |
| 67 | routes.py:2644 | POST /admin/evaluation/build-spectrograms | flash "Помилка запуску процесу.", no traceback | C, G | P2 |
| 68 | routes.py:2595-2605 + utils.py:1464, 1534 | `process_spectrograms_background` / `generate_spectrogram_image` | background thread uses `print()`; per-file failures printed without segment id; thread has no try, a crash is only stderr | J, K | P2 |

### 2d. Internal info leaked to UI (exhaustive)

| # | file:line | endpoint | what leaks | sev |
|---|---|---|---|---|
| 69 | pam_upload_utils.py:339-344 → template pam_verification_upload.html:316 | POST /pam/verification/upload/process (role manager) | `stats['error_files'][].error = str(e)` of psycopg2/SQLAlchemy/OS errors (SQL text, params, server paths) rendered in the upload report | P1 |
| 70 | pam_upload_utils.py:390 → routes.py:416 | same | `raise ValueError(f"... ZIP файл: {str(e)}")` wraps any OSError (e.g. `[Errno 28] No space left on device: '/mnt/HC_Volume.../temp_.../x.zip'`) → shown to user and returned as **400** (server fault reported as user error) | P1 |
| 71 | pam_evaluation_utils.py:618 → routes.py:2460 | POST /admin/verification/cleanup (role **manager**) | `flash(f'Помилка очищення: {str(e)}')` raw DB error | P1 |
| 72 | pam_evaluation_utils.py:461 → routes.py:2428 | POST /admin/evaluation/recalculate (role manager, see decorator at 2368) | `flash(f'Помилка перерахунку метрик: {str(e)}')` | P1 |
| 73 | pam_evaluation_utils.py:826 → routes.py:3123 | POST /admin/evaluation/convert-to-flac (admin) | raw DB error in flash | P2 |
| 74 | biotope_autoassign.py:520 | /pam/admin/biotopes/auto-assign/status (admin) | `str(e)` stored as `error_message`, shown on admin page | P3 |
| 75 | routes.py:3588, 3632 | /api/pam/data-preview, /data-download | `str(ValueError)` e.g. `invalid literal for int() with base 10: 'x'` (English Python text) | P3 |
| 76 | routes.py:4991 | POST /api/pam/import | Any `ValueError` (also from pandas/psycopg deep inside) echoed verbatim; logged at ERROR as "validation error" without traceback | P2 |
| 77 | pam_upload_utils.py:59 | ZIP upload, per-file | `strptime`/`float` messages re-wrapped into user text | P3 |

### 2e. Wrong status codes

| # | file:line | endpoint | what happens | sev |
|---|---|---|---|---|
| 78 | routes.py:2204, 2216, 2184 → 2223 | GET /audio/segments/<id> | `abort(404)` (segment row missing / file missing) and `abort(403)` (path confinement, SEC-013) raise `HTTPException`, caught by `except Exception`, logged as ERROR "Error serving audio file ... 404 Not Found" and re-raised as **500**. Missing files for archived segments are by design, but here they produce 500 + error logs; the 403 security signal is lost | P1 |
| 79 | routes.py:2556, 2575, 2184 → 2582 | GET /audio/spectrograms/<id> | same as #78 | P1 |
| 80 | routes.py:1794 (also 2944) | POST /api/verification/submit, /pam/api/location/create | `request.json` on wrong Content-Type raises 415/400 `HTTPException` → caught → 500 "Помилка збереження ..." | P2 |
| 81 | routes.py:3024 | POST /pam/api/update-location/<id> | `try/finally` without `except`: any error → Flask HTML 500 to a JSON caller; `data.get` on `None` body → AttributeError 500 | P2 |
| 82 | routes.py:2717, 4104 | /pam/manage-locations, /pam/service-plan | try/finally only, default HTML 500 | P3 |
| 83 | routes.py:5402 | POST /api/pam/sample/upload-segment | `save_and_register_segment` returns 'error' for every cause (disk full, FK, ffmpeg) → generic 500 "Failed to save segment"; cause only in an untagged log line | P2 |
| 84 | routes.py:411, 4988 | ZIP upload / CSV import | intentional user validation (`ValueError` → 400) logged at **ERROR**; inflates error logs | P3 |
| 85 | utils.py:1874 | `get_occurrence_data` | logs every exception incl. user `ValueError` at ERROR with traceback, then re-raises to a 400 path | P3 |

### 2f. Generic messages and missing context (pattern G/L, representative, not exhaustive)

| # | file:line | endpoint | message | sev |
|---|---|---|---|---|
| 86 | routes.py:981, 1029, 1081, 1137, 1192, 1351, 1404, 1458 | dashboard APIs get-plot-data … get-locations-map | `'Internal server error'`; traceback logged as a separate format_exc record; no species/params/user | P2 |
| 87 | routes.py:3292, 3446, 3919, 4574, 4994, 5236, 5406, 5658, 5690, 5813 | trends, species-dynamics, yearly-trends-table, weather, import, sample, cooccurrence | `'Internal server error'` (exc_info kept) | P3 |
| 88 | gap_routes.py:235 | all /api/pam/gap-analysis/* | `'Server error'` (exc_info kept, name of sub-API in log) | P3 |
| 89 | routes.py:4393, 4417 | service-visit update/delete | `'Помилка сервера.'` (exc_info + visit id kept) | P3 |
| 90 | routes.py:468, 578, 711, 811, 871 | verification segments/verifiers/priorities/verify, evaluation/results | page redirect + flash, traceback as separate record; evaluation_results additionally opens a **new DB connection inside the error handler** for diagnostics (871-890) | P3 |
| 91 | routes.py:300-312, 343-380, 860-870 | verification upload, evaluation results | INFO noise (per-request config dumps, per-species logistic dumps); upload rejections logged at ERROR | P3 |
| 92 | about 45 sites | all 400/403/404 returns (e.g. routes.py:952, 957, 1804, 1821, 1837, 2842, 2844, 2969, 3032-3040, 4307, 4344-4346, 4969, 5207, 5315) | not logged; access-denied (403) carries no user/ids, so permission complaints cannot be traced | P2 for 403, P3 for 400 |
| 93 | routes.py:5655, 5687, 5810 | cooccurrence run/sweep/export | domain `CooccurrenceError` → 400 with good Ukrainian text (good pattern) but not logged | P3 |

Good patterns already present (keep and extend): `biotope_autoassign.py` (`[pam-biotope-autoassign]`, `logger.exception`), `routes.py:264` (`logger.exception`), `routes.py:4320/4386` (4xx at WARNING), `routes.py:4210/4390/4414` (ids in message + exc_info), co-occurrence domain-error class, `gap_routes._json_endpoint` (single uniform wrapper).

---

## 3. Proposed log tags (from the actual route structure)

Format to copy from `[ct-upload]`: `[tag] <action> <OUTCOME> user=<id> <ids/params>`; `logger.exception` on 5xx, `logger.warning` on 403/409, `logger.info` on 400; client 5xx body `'<Ukrainian text> [ExcName]'`.

| Tag | Routes / code it covers |
|---|---|
| `[pam-dashboard]` | /pam, /pam/pam_detailed, /pam/pam_overview, /api/pam/get-plot-data, get-barchart-data, get-time-scatter-data, get-species-summary, get-unique-points, get-filters-data, get-species-ranking, get-overview-stats, get-locations-map, get-weather-overlay, export-detailed-data |
| `[pam-db]` | read helpers in utils.py (`get_filtered_detections`, `get_daily_detection_counts`, ... `get_weather_data`) with the function name as action, e.g. `[pam-db] get_species_ranking FAILED species=... model=...` |
| `[pam-verify]` | /pam/verification/verify, /api/verification/next-segment, /api/verification/submit, /pam/verification/segments, /api/verification/segments, /api/verification/stats, /api/verification/filter-options, /api/verification/consensus-status, /pam/verification/verifiers, /pam/verification/priorities, /api/pam/verification/top-verifiers |
| `[pam-verify-upload]` | /pam/verification/upload, /pam/verification/upload/process, pam_upload_utils.py |
| `[pam-media]` | /audio/segments/<id>, /audio/spectrograms/<id>, /admin/evaluation/build-spectrograms, `generate_spectrogram_image`, `process_spectrograms_background` |
| `[pam-eval]` | /pam/evaluation/results, /api/evaluation/detailed-results, /api/pam/evaluation/thresholds, /pam/evaluation/species/<id>, /pam/evaluation/export-species/<id>, /admin/evaluation/recalculate, pam_evaluation_utils.py |
| `[pam-maint]` | destructive/maintenance admin actions: /admin/verification/cleanup, /admin/evaluation/convert-to-flac, pam_linking_utils.py (log every deleted path and every failed archive update) |
| `[pam-import]` | /pam/import, /api/pam/import, pam_import_utils.py (per-file and per-row skip counts + reasons) |
| `[pam-sample]` | /pam/verification/sample-upload, /pam/verification/segment-window, /api/pam/sample/species, plan-window, prepare, upload-segment, pam_segment_sampling.py |
| `[pam-locations]` | /pam/manage-locations, /pam/api/location/<id>, /pam/api/location/create, /pam/api/update-location/<id>, /pam/api/location/<id>/delete, /pam/location/<id>/coverage |
| `[pam-service]` | /pam/service-plan, /api/pam/locations-with-status, /api/pam/location/<id>/service-history, /api/pam/service-log/create, /api/pam/service-visit/<id>/update, /delete, service_forecast.py |
| `[pam-trends]` | /pam/trends, /api/pam/yearly-trends, /pam/yearly-table, /api/pam/yearly-trends-table, /api/pam/get-trends-filters, /pam/species-dashboard, /api/pam/species-dynamics |
| `[pam-export]` | /pam/data-export, /api/pam/get-taxonomic-filters, /api/pam/data-preview, /api/pam/data-download, `get_occurrence_data` |
| `[pam-gap]` | /pam/gap-analysis, /api/pam/gap-analysis/grid, cell/<code>, kernel, biotopes (one change in `_json_endpoint` covers all) |
| `[pam-cooc]` | /pam/cooccurrence, /api/pam/cooccurrence/run, sweep, export, cooccurrence.py |
| `[pam-admin]` | /pam/admin, /pam/admin/species, /pam/admin/species/new, /pam/admin/species/<id> |
| `[pam-biotope-autoassign]` | existing: /pam/admin/biotopes/auto-assign(+/status), biotope_autoassign.py |
| `[pam-analytics-calc]` | pam_analytics_calculator.py batch job (switch from root `logging` to a named logger, non-zero exit on failure) |

### Suggested fix order
1. P1 silent data loss in import/upload (#1-3, #5-6, #17) and leaks to non-admin users (#69-72).
2. HTTPException-to-500 in media endpoints (#78-79): add `except HTTPException: raise` before `except Exception`.
3. Pattern A: make helpers re-raise (or return an explicit error flag) so dashboard APIs answer 5xx with a message instead of an empty 200 (#26-49).
4. Mechanical sweep: replace `logger.error(f"...{e}")` + `format_exc` pairs with `logger.exception('[tag] ... user=... ids=...')`, append `[ExcName]` to 5xx bodies, log 403 at WARNING with user and ids.
