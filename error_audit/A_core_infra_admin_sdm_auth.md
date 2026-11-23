# biomon core: error-handling and logging audit

Date: 2026-11-04. Mode: READ-ONLY static review (no code executed, no DB touched).
Scope: `app/__init__.py`, `app/extensions.py`, `app/seo.py`, `app/commands.py`, `app/routes/`,
`app/admin/`, `app/utils/`, `app/models/`, `app/backup/`, `app/sdm/` (submodule shared-sdm, tests and
migrations skipped), `wsgi.py`, `config.py`, `deploy/`.
Out of scope: `app/camera_traps`, `app/pam`, `app/docs` (submodules, only touched where they change
app-wide behaviour).

Auth lives in `app/routes/main.py` (login, register, confirm, resend, logout, profile),
`app/extensions.py` (`unauthorized` handler), `app/utils/decorators.py` (`role_required`),
`app/models/__init__.py` (`load_user`), `app/utils/tokens.py`.

Versions (requirements.txt / venv): Flask 3.1.1, Werkzeug 3.1.3, gunicorn 23.0.0, Flask-Limiter 4.1.1.

---

## 1. Infrastructure findings

### 1.1 Logging configuration: there is none, and the effective level changes at runtime

* No `logging.basicConfig`, `dictConfig`, `setLevel` or handler setup anywhere in the core
  (`app/__init__.py`, `config.py`, `wsgi.py`). `ProductionConfig.init_app` is a no-op.
* Flask therefore lazily attaches its `default_handler` to the `app` logger on first use:
  format `[%(asctime)s] %(levelname)s in %(module)s: %(message)s`, stream = `wsgi.errors`
  (gunicorn error log = stderr = journald under `biomon.service`). Timestamp and module are present;
  logger name, request id, user, IP and path are not.
* **Effective level is WARNING in production.** Flask only lowers the level when `DEBUG=True`;
  the root logger is unconfigured, so `app` inherits WARNING. Every `current_app.logger.info(...)`
  in the core is dropped: self-registration, email confirmed, password/username changed, access
  review applied, verifier grants, verification decisions, "Email sent to", purge counts.
  These are exactly the audit-trail lines for security-relevant actions.
* **Runtime side effect from submodules.** `app/camera_traps/analytics_calculator.py:23` and
  `app/pam/pam_analytics_calculator.py:13` call `logging.basicConfig(level=INFO, ...)` at import time.
  `camera_traps/routes.py:6774/6825` imports the CT calculator lazily, so in a gunicorn worker:
  - before anybody opens the analytics trigger/status endpoint: INFO is dropped;
  - after it: the root logger gets an INFO handler, INFO lines suddenly appear, and every `app`
    record is printed **twice** (Flask default handler on `app` + propagation to the root handler,
    with two different formats).
  Behaviour differs per worker (3 workers) and resets on every restart. Logs are non-deterministic.
* **Module loggers** (`app.backup.*`, `adapters.worker`, `adapters.pipeline`,
  `adapters.prediction_importer`) have no handler of their own. `adapters.*` is not under `app`, so in
  the `sdm-worker` service (and in the web process before the side effect above) records reach only
  Python's `lastResort` handler: WARNING+ only, format `%(message)s` (no timestamp, no level, no name).
  All `log.info("[job=%s %d%%] ...")` progress lines of the SDM worker never reach journald.
  journald adds its own timestamp, so the missing timestamp is cosmetic there; the missing INFO is not.
* Flask-Limiter logs breaches at INFO on logger `flask-limiter` (dropped). No `RATELIMIT_STORAGE_URI`
  is configured, so storage is `memory://`, per gunicorn worker: the effective limits are
  roughly x3 (3 workers) and reset on restart.
* No request id, no user id in log records. Some messages add `user_id=` by hand, most do not.
* `TRUSTED_PROXY_COUNT` defaults to 0. If production does not set it to 1, `request.remote_addr` is
  always `127.0.0.1` behind nginx: every "Failed login ... from 127.0.0.1" is useless and all
  visitors share one rate-limit bucket (5 login POSTs/min for the whole site). Cannot verify from the
  repo (server `.env` not available); check `grep TRUSTED_PROXY_COUNT /var/www/biomon/.env`.

### 1.2 Error handlers: only CSRFError

`app.register_error_handler(CSRFError, handle_csrf_error)` (`app/extensions.py:48`) is the only
handler in the whole app (no `errorhandler` in any blueprint or submodule). No `templates/errors/`
directory exists. What a user gets today:

| Status | Trigger | HTML page request | AJAX / fetch request | Logged? |
|---|---|---|---|---|
| 400 CSRF | expired token | Werkzeug default "400 Bad Request: The CSRF token has expired." (English, unstyled, no way back) | JSON `{code:'csrf_expired'}` **only** if `request.is_json` or `/api/` in path. FormData fetches (SDM enqueue, CT/PAM uploads outside `/api/`) get the HTML page | No |
| 400 other | `abort(400)`, bad form data | Werkzeug default HTML | Werkzeug default HTML | No |
| 401 | session expired on AJAX | n/a | `unauthorized()` always flashes and 302-redirects to login; fetch follows, gets 200 HTML, `r.json()` throws, UI shows a generic failure | No |
| 403 | `role_required` / `abort(403)` | Werkzeug default HTML, English | Werkzeug default HTML | No |
| 404 | `get_or_404`, unknown URL | Werkzeug default HTML, English, no site chrome | HTML | No |
| 405 | wrong method | Werkzeug default HTML | HTML | No |
| 413 | body/form limits | Werkzeug default HTML | HTML (upload JS has to guess) | No |
| 429 | Flask-Limiter | Flask-Limiter default HTML "429 Too Many Requests: 5 per 1 minute" | HTML | INFO only (dropped) |
| 500 / unhandled | any uncaught exception | Werkzeug "Internal Server Error" HTML | HTML; e.g. SDM dashboard `alert()` shows `HTTP 500: <!doctype html><html lang=en>...` | Yes: Flask `log_exception` -> `app` logger, ERROR with traceback, method and path, no user |

Note: Flask's `log_exception` does give a traceback for unhandled 500s, so the safety net for
"crash" is acceptable. The problem class is the *caught* exceptions (section 2) that never reach it.

### 1.3 Body-size limits

* `MAX_CONTENT_LENGTH` is **not set** anywhere (Flask default `None` = unlimited). Flask 3.1 defaults
  still apply: `MAX_FORM_MEMORY_SIZE = 500_000` bytes per non-file field and `MAX_FORM_PARTS = 1000`.
  A form with more than 1000 fields (for example `access_review_apply` with many ticked `pair`
  checkboxes, or a large admin user form) raises 413 with the raw Werkzeug page and no log line.
* nginx config is not in this repo nor in `server-ops` (`server-ops/etc/sites.d/biomon.conf.example`
  is a health-check config, not nginx). `client_max_body_size` cannot be verified. Whatever it is,
  nginx answers oversize bodies itself with its own HTML 413 that Flask never sees and never logs.
* `config.py:102` `PAM_MAX_UPLOAD_SIZE = 1000 * 1024 * 1024  # 500 MB` value and comment disagree
  (1000 MiB). The app-level check runs only after the whole body was received.
* gunicorn: no config file in the repo. README says `--bind 0.0.0.0:8003`, server notes say
  `127.0.0.1:8003`. No `--access-logfile` / `--log-level` documented. Unverifiable here.

### 1.4 Proposal

**a) One logging setup, called from `create_app`** — new `app/logging_setup.py`:

```python
class RequestContextFilter(logging.Filter):
    def filter(self, record):
        if has_request_context():
            record.request_id = getattr(g, 'request_id', '-')
            record.user_id = (current_user.get_id() if current_user and
                              current_user.is_authenticated else '-')
            record.ip = request.remote_addr
            record.path = f'{request.method} {request.path}'
        else:
            record.request_id = record.user_id = record.ip = record.path = '-'
        return True

FORMAT = ('%(asctime)s %(levelname)s %(name)s '
          '[rid=%(request_id)s uid=%(user_id)s ip=%(ip)s %(path)s] %(message)s')

def configure_logging(app):
    handler = logging.StreamHandler(sys.stderr)          # -> gunicorn -> journald
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RequestContextFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]                          # neutralise later basicConfig()
    root.setLevel(app.config.get('LOG_LEVEL', 'INFO'))
    app.logger.handlers.clear()                           # drop Flask default_handler (no dupes)
    app.logger.propagate = True
    for noisy in ('werkzeug', 'urllib3', 'googleapiclient'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
```

* Call it first thing in `create_app` (skip or keep in TESTING as needed) and also in the SDM worker
  (it runs `flask sdm worker`, so `create_app` already runs: one call covers both services).
* Because the root handler is installed first, the later `basicConfig()` in the CT/PAM calculators
  becomes a no-op (basicConfig does nothing when the root has handlers). The calls themselves should
  still be removed, but they live in public shared submodules: do it as a separate PR in
  shared-ct/shared-pam, not as a side effect.
* Request id: `before_request` sets `g.request_id = request.headers.get('X-Request-ID') or uuid4().hex[:12]`;
  `after_request` echoes it as `X-Request-ID`. In nginx add `proxy_set_header X-Request-ID $request_id;`
  so nginx access log and app log share the id. Error pages and JSON errors show the id
  ("quote this code to the admin"), which turns user reports into one `journalctl | grep`.
* Set `RATELIMIT_STORAGE_URI` (Redis, or at least document the x3 effect) and
  `logging.getLogger('flask-limiter').setLevel(INFO)` once INFO is on.

**b) Global error handlers** — new `app/errors.py`, registered in `create_app`:

```python
def wants_json():
    if request.is_json or '/api/' in request.path:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    best = request.accept_mimetypes.best_match(['application/json', 'text/html'])
    return best == 'application/json' and \
        request.accept_mimetypes[best] > request.accept_mimetypes['text/html']

def _respond(status, code, message):
    rid = getattr(g, 'request_id', None)
    if wants_json():
        return jsonify(error=message, code=code, request_id=rid), status
    return render_template(f'errors/{status}.html', message=message,
                           request_id=rid), status   # fall back to errors/generic.html

def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def http_error(e):
        if e.code in (401, 403, 404, 405, 413, 429):
            current_app.logger.warning('[http] %s %s', e.code, e.description)
        return _respond(e.code, e.name.lower().replace(' ', '_'), e.description)

    @app.errorhandler(Exception)
    def unhandled(e):
        if isinstance(e, HTTPException):
            return http_error(e)
        db.session.rollback()
        current_app.logger.exception('[unhandled] %s', type(e).__name__)
        return _respond(500, 'internal_error', _('Server error. Reference: %(rid)s', rid=...))
```

* Replace `handle_csrf_error` with the same `wants_json()` test (fixes FormData fetches) and log it at
  WARNING (`[csrf]`, with path and whether the session had a token).
* `unauthorized()` (`extensions.py:24`): when `wants_json()`, return `401 {code:'login_required'}`
  instead of flash+redirect.
* Templates: `app/templates/errors/{400,403,404,413,429,500}.html` extending `base.html`, translated,
  with a "back" link and the request id. The 500 template must not touch the DB (session may be
  broken) and must tolerate a missing `g.lang_code` (error raised before `before_request` finished).
* Set `MAX_CONTENT_LENGTH` explicitly to a value just above nginx `client_max_body_size`, so
  the app, not nginx, produces the 413 and it reaches the JSON/HTML handler and the log. Decide
  `MAX_FORM_PARTS` consciously (raise it for admin bulk forms).

**c) `log_and_respond` helper** for the many caught-exception sites. Put it in `app/utils/errors.py`
(core only; submodules can import it guarded, or keep their own copy):

```python
def log_and_respond(tag, exc, status=500, *, user_message=None, redirect_to=None,
                    level=logging.ERROR, **ctx):
    """Log with tag + context + traceback, roll back, answer HTML or JSON."""
    db.session.rollback()
    extra = ' '.join(f'{k}={v!r}' for k, v in ctx.items())
    current_app.logger.log(level, '[%s] %s: %s %s', tag, type(exc).__name__, exc, extra,
                           exc_info=exc if level >= logging.ERROR else None)
    msg = user_message or _('Помилка збереження. Код: %(rid)s', rid=g.get('request_id', '-'))
    if wants_json():
        return jsonify(error=msg, code=tag, request_id=g.get('request_id')), status
    flash(msg, 'danger')
    return redirect(redirect_to or request.referrer or url_for('main.root'))
```

Typical replacement for the admin pattern:
`except Exception as e: return log_and_respond('admin', e, user_id=user.id, action='delete_user', redirect_to=url_for(...))`.
For known domain errors (`ValueError` from services, `IntegrityError` for duplicates) keep a
specific branch with a precise message at WARNING, and let only the generic branch use ERROR.

---

## 2. Registry

Severity: P1 = nothing logged and the user sees nothing useful / silent data loss / leak;
P2 = logged without traceback or context, or generic/misleading message, wrong status;
P3 = cosmetic or low impact.

| # | file:line | function | what happens | problem class | sev |
|---|---|---|---|---|---|
| 1 | app/__init__.py:88-150, config.py | create_app | No logging config; prod effective level WARNING, all `logger.info` audit lines dropped (registration, password change, access review, verifier grants, decisions) | missing logs (infra) | P1 |
| 2 | app/camera_traps/analytics_calculator.py:23, app/pam/pam_analytics_calculator.py:13 (lazy import camera_traps/routes.py:6774) | module import | `basicConfig(INFO)` at import in a web worker: level flips at runtime, every `app` record duplicated in two formats, per-worker differences | inconsistent logging (infra, submodule) | P2 |
| 3 | app/__init__.py / extensions.py | (missing) | No error handlers for 400/403/404/405/413/429/500; AJAX callers receive Werkzeug HTML, users see English unstyled pages | wrong content type / generic msg | P2 |
| 4 | app/extensions.py:31-39 | handle_csrf_error | JSON only for `is_json` or `/api/`; FormData fetches get HTML 400; failure not logged | wrong content type, missing log | P2 |
| 5 | app/extensions.py:24-29 | unauthorized | Session expiry during AJAX -> 302 to login HTML -> fetch parses HTML, generic "failed" | wrong status for AJAX | P2 |
| 6 | config.py (absent) | MAX_CONTENT_LENGTH | Unset; Flask 413 only from MAX_FORM_PARTS=1000/MAX_FORM_MEMORY_SIZE, raw HTML, not logged; nginx limit unverifiable | config gap | P2 |
| 7 | config.py:102 | PAM_MAX_UPLOAD_SIZE | Value 1000 MiB, comment says 500 MB | misleading config | P3 |
| 8 | app/extensions.py:21 + config | limiter | `memory://` per worker (limits x3, reset on restart); breaches logged at INFO (dropped) | missing log / weak control | P2 |
| 9 | app/__init__.py:102 | create_app | `TRUSTED_PROXY_COUNT` default 0: behind nginx all IPs in logs and limiter keys are 127.0.0.1 unless prod env sets 1 (verify) | log context wrong | P2 (if unset) |
| 10 | app/routes/main.py:145-148 | login | Failure logged at WARNING with username, IP, UA, but no reason (unknown user vs wrong password) and the line is shared by both; user message is correctly generic | missing context | P3 |
| 11 | app/routes/main.py:127-133 | login | Correct password on inactive / unconfirmed account: flash only, no log line | missing log (auth) | P2 |
| 12 | app/routes/main.py:92-96 | contacts | DB save failure: `logger.error(f"...{e}")` without traceback; message lost for the visitor (only a generic flash) | no traceback | P2 |
| 13 | app/routes/main.py:206-211 | register | Registration failure: error without traceback; IntegrityError race (duplicate username/email) shown as generic "try later" | no traceback, generic msg | P2 |
| 14 | app/routes/main.py:256-260 | confirm_email | Commit failure: error without traceback | no traceback | P2 |
| 15 | app/routes/main.py:331, 347, 358 | profile | `db.session.commit()` unguarded for password, username (unique race -> IntegrityError), notification prefs -> raw 500 page; success info logs dropped (#1) | unhandled -> generic 500 | P2 |
| 16 | app/routes/main.py:371-377 | profile | CT/PAM stats failure: warning with `{e}`, no traceback; page silently shows no stats | no traceback | P3 |
| 17 | app/routes/main.py:411 | csp_report | Every CSP report logged at WARNING (report-only policy) -> noise that buries real warnings | log noise | P3 |
| 18 | app/utils/i18n.py:51-52 | before_request_handler | `except Exception: pass` on Babel `refresh()` on every request | silent swallow | P3 |
| 19 | app/utils/decorators.py:24 | role_required | 403 without log (who tried what) and HTML for JSON endpoints | missing log | P3 |
| 20 | app/utils/forms.py:114-116 | RegistrationForm.validate_website | Honeypot hit gives a field error, nothing logged (no bot statistics) | missing log | P3 |
| 21 | app/utils/emails.py:27-30 | _deliver | Send failure logged at ERROR without traceback (acceptable: SMTP message is informative); success at INFO with recipient addresses (PII in logs, dropped anyway by #1) | no traceback / PII | P3 |
| 22 | app/utils/notifications.py:95-101 | _deliver | Telegram/Discord failure WARNING with `{e}` only; fine for best-effort | no traceback | P3 |
| 23 | app/admin/routes.py:153-155 | create_user | `flash(f'Помилка при збереженні: {str(e)}')`: raw exception (SQL, constraint names, parameters) shown in UI; nothing logged | leak + missing log | P1 |
| 24 | app/admin/routes.py:250-252 | edit_user | Same pattern; plus `send_rights_granted_email` and logging run inside the same try after `commit()`: any error there flashes "Помилка при збереженні" although data was saved | leak, missing log, misleading msg | P1 |
| 25 | app/admin/routes.py:291-293 | delete_user | Raw exception in flash, nothing logged; successful deletion not logged either (no audit trail) | leak + missing log | P1 |
| 26 | app/admin/routes.py:520-522 | edit_institution | Raw exception in flash, no log; success flash is queued **before** `commit()`, so on failure the user sees both "успішно створено" and the error | leak, misleading msg | P1 |
| 27 | app/admin/routes.py:543-545 | delete_institution | Raw exception in flash, no log; success not logged (cascades user links) | leak + missing log | P1 |
| 28 | app/admin/routes.py:593-595 | edit_role | Raw exception in flash, no log; success flash before commit (as #26) | leak, misleading msg | P1 |
| 29 | app/admin/routes.py:620-622 | delete_role | Raw exception in flash, no log | leak + missing log | P1 |
| 30 | app/admin/routes.py:668-670 | update_submission_status | Raw exception in flash, no log | leak + missing log | P2 |
| 31 | app/admin/routes.py:683-685 | delete_submission | Raw exception in flash, no log; deletion not logged | leak + missing log | P2 |
| 32 | app/admin/routes.py:760-763 | decide_verification_request | Commit failure: `flash(f'...{e}')`, nothing logged | leak + missing log | P1 |
| 33 | app/admin/routes.py:379-383 | institution_verifiers_save | Good: `logger.exception` with context, generic JSON 500. Only issue: success line is INFO (dropped, #1) | reference pattern | ok |
| 34 | app/admin/routes.py:453-456 | access_review_apply | Good: `logger.exception`, translated flash. Message lacks user_id/params in the error line | missing context | P3 |
| 35 | app/admin/routes.py:504-507 | edit_institution | `ValueError` from service shown via `flash(str(e))`: intended (Ukrainian domain messages), but any other ValueError raised deeper would leak too | potential leak | P3 |
| 36 | app/admin/routes.py:329 | _verifier_cell_or_404 | `abort(403)` on a JSON endpoint -> HTML 403, JS shows generic "failed" | wrong content type | P3 |
| 37 | app/admin/routes.py (create/edit/delete user, institution, role) | several | No audit log for create/delete actions at all (only some edits log, at INFO) | missing audit log | P2 |
| 38 | app/admin/routes.py:151,155,248,252,293,497,513,522,542,577,587,595,612,619,622,670,685,762 | several | Untranslated f-string flashes mixed with `_()` ones; EN users get Ukrainian | cosmetic / i18n | P3 |
| 39 | app/admin/access_review.py:115 | _check | `ValueError` if params invalid; route sanitises params first, so unreachable in practice; otherwise raw 500 | unhandled | P3 |
| 40 | app/models/__init__.py:476 | load_user | `int(user_id)` on a tampered/legacy cookie would raise -> 500 on every page until cookie cleared (cookie is signed, so low risk) | unhandled | P3 |
| 41 | app/commands.py:163-166, 185-198 | send-id-reminders, purge-unconfirmed | No try/except: crash gives a click traceback into the cron log file (acceptable); INFO logs from helpers dropped, but `click.echo` reports counts | CLI | P3 |
| 42 | app/commands.py:260-269 | register_commands | SDM CLI ImportError -> warning (good). Any other exception in `register_sdm_cli` crashes app start | narrow except | P3 |
| 43 | app/backup/ct_csv.py:227-230, 266-273 | export_institution / run_backup | Good: per-backend and per-institution errors with `exc_info=True`, tag `[ct-csv-backup]`, reported back via click and exit 1. In CLI context the module logger has no handler -> lastResort, message only; INFO "rotated out" lines lost | logging infra | P3 |
| 44 | app/backup/storage.py:188-196 | RcloneStorage.read_manifest | `StorageError` / bad JSON -> `{}` silently (local backend logs a warning for the same case). Effect: re-export every night, never noticed | silent swallow | P3 |
| 45 | app/backup/storage.py:275-279 | _silent_unlink | `except OSError: pass` on temp-file cleanup; leaks `.tmp` files on a 96%-full disk without a trace | silent swallow | P3 |
| 46 | app/seo.py:102-138 | sitemap | Docs sitemap DB error -> raw 500 for Googlebot; logged by Flask (traceback) | unhandled | P3 |
| 47 | app/sdm/routes.py:56-57 | _species_list | `except Exception: return []`, no log: DB down / SQL error = dashboard and map show "no species" as if there were no data | silent swallow (data looks empty) | P1 |
| 48 | app/sdm/routes.py:76-77 | _recent_jobs | Same: job list silently empty, active job not detected | silent swallow | P1 |
| 49 | app/sdm/routes.py:95-96 | _current_runs | Same: "no current models" | silent swallow | P1 |
| 50 | app/sdm/routes.py:222-223 | coefficients_view | Same: empty coefficient table on error | silent swallow | P1 |
| 51 | app/sdm/routes.py:152 | enqueue | `create_job` unguarded: DB error -> HTML 500 into a fetch, alert shows `HTTP 500: <!doctype html>...`; `task_name` from the form is not validated (unknown type fails later in the worker as "Unknown job_type") | unhandled, missing validation | P2 |
| 52 | app/sdm/routes.py:162 | job_status | `get_job_status` unguarded -> HTML 500; dashboard polling does `.catch(() => {})` so the progress bar just freezes | unhandled + client swallow | P2 |
| 53 | app/sdm/routes.py:165 / adapters/worker.py:153-156, 263 | job_status | Full Python traceback (`traceback.format_exc()`) stored in `sdm_job_queue.error` and returned to the browser (analyst role and up) | internal info leak (authenticated) | P2 |
| 54 | app/sdm/routes.py:266-268 | api_predictions | Good: `exc_info=True`, generic JSON 500. Uses f-string, no tag/user/params in the line | missing context | P3 |
| 55 | app/sdm/adapters/worker.py:159-177 | Worker._process_job | Final status UPDATE is outside any try: if the DB write fails the exception kills the worker loop, job stays `running` forever (no stale-job reaper seen), systemd restarts the worker | silent stuck job | P1 |
| 56 | app/sdm/adapters/worker.py:197-198 | _update_progress | `log.warning` without exception text or traceback | no traceback | P3 |
| 57 | app/sdm/adapters/worker.py:49 + deploy/sdm-worker.service | module logger | Logger `adapters.worker` is outside the `app` hierarchy and nothing configures root: all INFO progress lines (`[job=... pct]`, "starting job", "→ success") never reach journald; ERRORs arrive without level/timestamp/name | missing logs (infra) | P2 |
| 58 | app/sdm/adapters/pipeline.py:649-674 + prediction_importer.py:87-88, 144-147, 223-229, 298-300 | _step_import_predictions | No `predictions_*.csv`, missing columns or unreadable CSV -> `log.warning/error` and 0 rows, but the step still returns `StepResult.done`; next step `_step_mark_current` makes this empty run current -> map goes blank, job reported "success" | silent data loss | P1 |
| 59 | app/sdm/adapters/pipeline.py:778-781 | run_pipeline | Good: `log.exception` + progress message. Exception text goes to the job log shown in UI (paths, SQL) | internal info in UI | P3 |
| 60 | app/sdm/adapters/r_runner.py:91-98 | _run_r | Timeout / missing Rscript / non-zero exit returned as `RResult(ok=False)` without a log line here; caller puts the last 500 chars of stderr into the job log (fine), nothing at ERROR | missing log | P3 |
| 61 | app/sdm/adapters/gee_backend.py:94-98, 119-120; ct_reader.py:72-74; biomon.py:161-162 | config resolvers | `except (ImportError, RuntimeError): pass` to fall back from Flask config to env: intentional, but a RuntimeError other than "no app context" is also swallowed | broad swallow | P3 |
| 62 | app/sdm/core/predictors/gee_source.py:237-244 | fetch | GEE failure turned into `diagnostics` list, no log line; depends on the caller surfacing diagnostics | missing log | P3 |
| 63 | app/sdm/adapters/biomon_cli.py:166-169 | check | `alembic_version` query `except Exception: av = None` (shows "no version"); acceptable for a diagnostic command | silent swallow | P3 |
| 64 | app/sdm/adapters/grid_writer.py:50, predictor_writer.py:69 | docstrings | `print()` only inside usage examples in docstrings; no real `print()` in scope. biomon_cli uses `click.echo` (correct for CLI) | ok | ok |

Counts: P1 = 15 (rows 1, 23-29, 32, 47-50, 55, 58), P2 = 19, P3 = 28, ok/reference = 2 (33, 64).
The P1 list collapses into four fixes: logging setup (#1), admin `str(e)` pattern (#23-32), SDM
`except: return []` helpers (#47-50), SDM worker/import robustness (#55, #58).

---

## 3. Proposed tags

Convention already in use: `[ct-upload]` (camera_traps) and `[ct-csv-backup]` (backup). Keep the
format `[module-area] message key=value ...` at the start of the message so
`journalctl -u biomon | grep '\[admin'` works without structured logging.

| Tag | Where | Notes |
|---|---|---|
| `[auth-login]` | main.login | `result=bad_password|unknown_user|inactive|unconfirmed|ok`, username, ip. Reason in the log only, never in the UI |
| `[auth-register]` | main.register, utils.registration | include honeypot / recaptcha rejects |
| `[auth-confirm]` | main.confirm_email, resend_confirmation, utils.tokens | expired vs bad signature |
| `[auth-profile]` | main.profile | password/username/prefs change |
| `[auth-perm]` | utils.decorators.role_required, extensions.unauthorized, `_verifier_cell_or_404` | 401/403 with endpoint and roles |
| `[csrf]` | CSRF handler | path, wants_json |
| `[http]` | global HTTPException handler | 404/405/413/429 at WARNING (404 maybe INFO) |
| `[unhandled]` | global Exception handler | ERROR + traceback |
| `[admin-user]` | create/edit/delete user | actor uid, target uid, action |
| `[admin-inst]` | institution CRUD | |
| `[admin-role]` | role CRUD | |
| `[admin-access]` | institution_verifiers_save, access_review_apply | |
| `[admin-verif]` | decide_verification_request | |
| `[admin-contact]` | contact submissions status/delete | |
| `[contact]` | main.contacts | |
| `[mail]` | utils.emails | recipient domain only, not full address |
| `[notify]` | utils.notifications | telegram/discord |
| `[ct-csv-backup]` | app/backup | already in use, keep |
| `[sdm-ui]` | sdm/routes.py helpers and views | |
| `[sdm-api]` | sdm api_predictions, job_status | |
| `[sdm-queue]` | sdm enqueue / create_job / get_job_status | |
| `[sdm-worker]` | adapters/worker.py | job id always present |
| `[sdm-pipeline]` | adapters/pipeline.py, r_runner, prediction_importer | step name, run id |
| `[sdm-gee]` | gee_backend, gee_source | |
| `[cli]` | app/commands.py | command name |
| `[csp]` | csp_report | consider INFO or sampling |
| `[seo]` | sitemap/robots | |

Note on submodules: `app/sdm` (shared-sdm), `app/camera_traps`, `app/pam` are public submodules shared
with myproject. Fixes #2, #47-#62 must be made in their own repos and bumped here, not edited in
place as a side effect of a biomon change.
