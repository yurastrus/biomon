# SPDX-License-Identifier: AGPL-3.0-only
"""Site-wide error responses, request ids and the ``log_and_respond`` helper.

Error audit 2026-11-04 (error_audit/ERROR_AUDIT.md §3.1). Before this module
only CSRFError had a handler: every other 4xx/5xx and every unhandled
exception came back as Werkzeug's English HTML page, also to AJAX callers that
then failed to parse it ("Unexpected token '<'"), and nothing tied a user's
report to a log line.

Contract:
* every request gets ``g.request_id`` (echoed in the ``X-Request-ID`` header);
* JSON callers get ``{"error": <user text>, "code": <slug>, "request_id": <rid>}``;
* browsers get a translated page (templates/errors/error.html) with the code;
* 5xx are logged with traceback, expected 4xx at INFO/WARNING without one.

Shared submodules (camera_traps, pam, sdm) may import ``log_and_respond`` and
``wants_json`` from here; myproject ships the same module.
"""
import logging
import re
import uuid

from flask import (current_app, flash, g, jsonify, redirect, render_template,
                   request, url_for)
from flask_babel import gettext as _
from werkzeug.exceptions import HTTPException

_RID_RE = re.compile(r'^[A-Za-z0-9-]{8,64}$')

# Level per status for logging HTTP errors. 404 is mostly bots probing
# (/wp-admin, .env) and stays at INFO so it doesn't drown real warnings.
_HTTP_LOG_LEVEL = {404: logging.INFO, 405: logging.INFO}


# ── request id ──────────────────────────────────────────────────────────────

def init_request_id(app):
    """Register request-id hooks. Call before any other before_request hook."""

    @app.before_request
    def _assign_request_id():
        incoming = request.headers.get('X-Request-ID', '')
        # Accept nginx's $request_id (32 hex) but never echo arbitrary client
        # input into logs: anything unexpected gets a fresh id.
        g.request_id = incoming if _RID_RE.match(incoming) else uuid.uuid4().hex[:12]

    @app.after_request
    def _echo_request_id(response):
        rid = getattr(g, 'request_id', None)
        if rid:
            response.headers['X-Request-ID'] = rid
        return response


def current_request_id():
    return getattr(g, 'request_id', None) or '-'


# ── content negotiation ─────────────────────────────────────────────────────

def wants_json():
    """True when the caller is script code that expects JSON, not a page."""
    if request.is_json or '/api/' in request.path:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.accept_mimetypes
    best = accept.best_match(['application/json', 'text/html'])
    return best == 'application/json' and accept[best] > accept['text/html']


# ── responses ───────────────────────────────────────────────────────────────

def _default_message(status):
    messages = {
        400: _('Некоректний запит.'),
        401: _('Потрібно увійти в систему.'),
        403: _('Недостатньо прав для цієї дії.'),
        404: _('Сторінку не знайдено.'),
        405: _('Цей метод запиту не підтримується.'),
        413: _('Завеликий файл або запит.'),
        429: _('Забагато запитів. Спробуйте трохи пізніше.'),
    }
    if status >= 500:
        return _('Помилка на сервері. Ми вже маємо запис про неї в журналі.')
    return messages.get(status, _('Помилка запиту.'))


def _render_error_page(status, message, rid):
    """Render the HTML error page; fall back to bare HTML if that fails.

    The page extends base.html, which touches current_user (DB) and url_for.
    In a 500 the DB may be the thing that is broken, so a failure here must
    not turn into a second, uglier error.
    """
    try:
        return render_template('errors/error.html', status=status,
                               message=message, request_id=rid), status
    except Exception:
        current_app.logger.warning('[http] error page render failed for %s',
                                   status, exc_info=True)
        from markupsafe import escape
        body = (f'<!doctype html><meta charset="utf-8"><title>{status}</title>'
                f'<h1>{status}</h1><p>{escape(message)}</p>'
                f'<p>Код запиту / Request id: <code>{escape(rid)}</code></p>')
        return body, status, {'Content-Type': 'text/html; charset=utf-8'}


def error_response(status, message, code=None, headers=None):
    """JSON or HTML error response carrying the request id."""
    rid = current_request_id()
    code = code or f'http_{status}'
    if wants_json():
        resp = jsonify(error=message, code=code, request_id=rid)
        resp.status_code = status
    else:
        rv = _render_error_page(status, message, rid)
        resp = current_app.make_response(rv)
    for key, value in (headers or []):
        if key.lower() not in ('content-type', 'content-length'):
            resp.headers[key] = value
    return resp


def _adopt_url_language():
    """For an unmatched URL (/en/no-such-page) no view_args carry lang_code,
    so the locale fell back to session/default. Take it from the path."""
    if request.view_args:
        return
    first = request.path.strip('/').split('/', 1)[0]
    if first in current_app.config.get('LANGUAGES', {}) and getattr(g, 'lang_code', None) != first:
        g.lang_code = first
        try:
            from flask_babel import refresh
            refresh()
        except Exception:
            pass


def _rollback_quietly():
    try:
        from app.extensions import db
        db.session.rollback()
    except Exception:
        pass


def register_error_handlers(app):

    @app.errorhandler(HTTPException)
    def _handle_http_exception(e):
        # Redirects and other non-error HTTPExceptions pass through untouched,
        # as do aborts that already carry a full response (abort(resp)).
        if e.code is None or e.code < 400 or e.response is not None:
            return e
        level = _HTTP_LOG_LEVEL.get(e.code, logging.ERROR if e.code >= 500
                                    else logging.WARNING)
        current_app.logger.log(level, '[http] %s %s %s', e.code, e.name,
                               request.full_path.rstrip('?'))
        # Keep a description a developer wrote via abort(400, "..."); replace
        # Werkzeug's generic English text with our translated one.
        _adopt_url_language()
        custom = e.description and e.description != type(e).description
        message = e.description if custom else _default_message(e.code)
        return error_response(e.code, message, code=f'http_{e.code}',
                              headers=e.get_headers())

    @app.errorhandler(Exception)
    def _handle_unhandled(e):
        if isinstance(e, HTTPException):  # defensive; normally routed above
            return _handle_http_exception(e)
        # Keep the interactive debugger / test-time propagation unchanged.
        if current_app.debug or (current_app.testing and
                                 current_app.config.get('PROPAGATE_EXCEPTIONS') is not False):
            raise e
        _rollback_quietly()
        current_app.logger.error('[unhandled] %s: %s', type(e).__name__, e,
                                 exc_info=e)
        rid = current_request_id()
        message = _('Помилка на сервері. Повідомте адміністратору код: %(rid)s',
                    rid=rid)
        return error_response(500, f'{message} [{type(e).__name__}]',
                              code='internal_error')


# ── helper for caught exceptions ────────────────────────────────────────────

def log_and_respond(tag, exc, status=500, *, user_message=None, redirect_to=None,
                    level=None, **ctx):
    """Log a caught exception with tag + context, roll back, answer the caller.

    ``level`` defaults to ERROR (with traceback) for 5xx and WARNING (without)
    for 4xx. JSON callers get the standard error body; page requests get a
    flash message and a redirect (``redirect_to``, else the referrer).

    Usage::

        except Exception as e:
            return log_and_respond('admin', e, user_id=user.id,
                                   action='delete_user',
                                   redirect_to=url_for('admin.users'))
    """
    _rollback_quietly()
    if level is None:
        level = logging.ERROR if status >= 500 else logging.WARNING
    extra = ' '.join(f'{k}={v!r}' for k, v in ctx.items())
    current_app.logger.log(level, '[%s] %s: %s %s', tag, type(exc).__name__,
                           exc, extra,
                           exc_info=exc if level >= logging.ERROR else None)
    rid = current_request_id()
    if user_message is None:
        user_message = _('Не вдалося виконати дію. Код запиту: %(rid)s', rid=rid)
    if status >= 500:
        user_message = f'{user_message} [{type(exc).__name__}]'
    if wants_json():
        resp = jsonify(error=user_message, code=tag, request_id=rid)
        resp.status_code = status
        return resp
    flash(user_message, 'danger')
    return redirect(redirect_to or _same_host_referrer() or url_for('main.root'))


def _same_host_referrer():
    """The Referer, but only if it points at this site (no open redirect)."""
    from urllib.parse import urlparse
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return ref
    return None
