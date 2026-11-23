/* SPDX-License-Identifier: AGPL-3.0-only
 *
 * Shared client-side error handling: window.HttpErr.
 *
 * Error audit 2026-11-04 (error_audit/ERROR_AUDIT.md §3.2). Generalises the
 * describeAjaxError()/logClientError() pair from camera_traps upload_fast so
 * every page can show an actionable message ("[HTTP 413] ...", "no
 * connection", "page is stale, reload") instead of a bare "Помилка", and
 * leave one tagged console line for debugging.
 *
 * Loaded by base.html (biomon and myproject ship the same file) outside any
 * block, before page scripts, not deferred. No jQuery dependency.
 * Translated strings come from window.HTTP_I18N (set in base.html).
 */
(function (window, document) {
    'use strict';

    var I18N = window.HTTP_I18N || {};
    var T = {
        unknown: I18N.unknown || 'Unknown error',
        noConn:  I18N.noConn  || 'No connection to the server (check the network)',
        stale:   I18N.stale   || 'The page is out of date. Reload it and try again.',
        reload:  I18N.reload  || 'Reload',
        code:    I18N.code    || 'Request id'
    };

    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    function snippet(text) {
        // Non-JSON body (proxy 502/504, HTML error page): short readable text.
        return String(text || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
    }

    function HttpError(status, message, body, url, requestId) {
        this.name = 'HttpError';
        this.status = status;
        this.message = message;
        this.body = body;
        this.url = url;
        this.requestId = requestId || null;
        this.stale = false;
    }
    HttpError.prototype = Object.create(Error.prototype);
    HttpError.prototype.constructor = HttpError;

    function isLoginRedirect(response) {
        return !!(response && response.redirected && /\/login(\b|\/|\?)/.test(response.url || ''));
    }

    function isStaleBody(body) {
        return !!(body && (body.code === 'csrf_expired' || body.code === 'login_required'));
    }

    /* fetch() wrapper: same-origin credentials, CSRF + XHR headers on writes,
     * JSON body encoding for plain objects, and one error type for every
     * failure. Resolves with parsed JSON (or text for non-JSON 2xx). */
    function fetchJSON(url, opts) {
        opts = Object.assign({ credentials: 'same-origin' }, opts || {});
        var method = (opts.method || 'GET').toUpperCase();
        var headers = new Headers(opts.headers || {});
        headers.set('X-Requested-With', 'XMLHttpRequest');
        if (!headers.has('Accept')) headers.set('Accept', 'application/json');
        if (method !== 'GET' && method !== 'HEAD' && !headers.has('X-CSRFToken')) {
            headers.set('X-CSRFToken', csrf());
        }
        if (opts.body && typeof opts.body === 'object' &&
            !(opts.body instanceof FormData) && !(opts.body instanceof Blob) &&
            !(opts.body instanceof URLSearchParams)) {
            opts.body = JSON.stringify(opts.body);
            if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
        }
        opts.headers = headers;

        return fetch(url, opts).then(function (response) {
            var type = response.headers.get('Content-Type') || '';
            var rid = response.headers.get('X-Request-ID');
            if (isLoginRedirect(response)) {
                var e = new HttpError(401, T.stale, null, url, rid);
                e.stale = true;
                throw e;
            }
            if (type.indexOf('application/json') !== -1) {
                return response.json().then(function (body) {
                    if (response.ok) return body;
                    var err = new HttpError(response.status,
                        (body && (body.error || body.message)) || response.statusText || T.unknown,
                        body, url, (body && body.request_id) || rid);
                    err.stale = isStaleBody(body);
                    throw err;
                });
            }
            return response.text().then(function (text) {
                if (response.ok) return text;
                throw new HttpError(response.status, snippet(text) || response.statusText || T.unknown,
                                    text, url, rid);
            });
        });
    }

    /* Human message for any error shape: HttpError, jqXHR, fetch TypeError,
     * plain Error. Output format matches upload_fast's describeAjaxError. */
    function describe(err, fallback) {
        if (!err) return fallback || T.unknown;
        if (err.stale) return T.stale;
        var status = (typeof err.status === 'number') ? err.status : null;
        var msg = '';
        var rid = err.requestId || null;
        if (err instanceof HttpError) {
            msg = err.message;
        } else if (err.responseJSON || typeof err.responseText === 'string') {   // jqXHR
            var j = err.responseJSON;
            if (j) {
                if (isStaleBody(j)) return T.stale;
                msg = j.error || j.message || '';
                rid = j.request_id || rid;
            }
            if (!msg && err.responseText) msg = snippet(err.responseText);
            if (!msg) msg = err.statusText || '';
            if (!rid && typeof err.getResponseHeader === 'function') {
                try { rid = err.getResponseHeader('X-Request-ID'); } catch (e) { /* ignore */ }
            }
        } else if (err instanceof TypeError) {      // fetch network failure
            return T.noConn + ' (' + err.message + ')';
        } else {
            msg = err.message || String(err);
        }
        if (!msg) msg = fallback || T.unknown;
        if (status === 0) return T.noConn + (msg ? ' (' + msg + ')' : '');
        var out = status ? '[HTTP ' + status + '] ' + msg : msg;
        if (rid && status && status >= 500 && out.indexOf(rid) === -1) {
            out += ' (' + T.code + ': ' + rid + ')';
        }
        return out;
    }

    /* One console line per failure, greppable in DevTools: [biomon][ctx]. */
    function log(context, err, extra) {
        var status = (err && typeof err.status === 'number') ? err.status : 'n/a';
        var detail = err && (err.body || err.responseJSON || err.responseText || err.message) || '';
        console.error(('[biomon][' + context + '] HTTP=' + status + ' ' + (extra || '')).trim(), detail, err);
    }

    /* Poll fn() (returns a promise) every intervalMs until it resolves to a
     * truthy "done" value, or fails maxFails times in a row (then onError),
     * or stop() is called. Replaces endless setInterval loops that ignored
     * errors. */
    function poll(fn, options) {
        options = Object.assign({ intervalMs: 3000, maxFails: 5, onError: null }, options || {});
        var fails = 0, stopped = false, timer = null;
        function tick() {
            if (stopped) return;
            Promise.resolve().then(fn).then(function (done) {
                fails = 0;
                if (!done && !stopped) timer = setTimeout(tick, options.intervalMs);
            }, function (err) {
                fails += 1;
                if (err && err.stale) { stopped = true; showStale(); return; }
                if (fails >= options.maxFails) {
                    stopped = true;
                    if (options.onError) options.onError(err);
                    return;
                }
                timer = setTimeout(tick, options.intervalMs * Math.min(fails + 1, 5));
            });
        }
        tick();
        return { stop: function () { stopped = true; clearTimeout(timer); } };
    }

    /* One banner with a reload button for an expired session / CSRF token. */
    function showStale() {
        if (document.getElementById('httperr-stale')) return;
        var bar = document.createElement('div');
        bar.id = 'httperr-stale';
        bar.setAttribute('role', 'alert');
        bar.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:10000;padding:10px 16px;' +
            'background:#fff3cd;color:#664d03;border-bottom:1px solid #ffda6a;text-align:center;';
        bar.appendChild(document.createTextNode(T.stale + ' '));
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = T.reload;
        btn.style.marginLeft = '8px';
        btn.addEventListener('click', function () { window.location.reload(); });
        bar.appendChild(btn);
        document.body.appendChild(bar);
    }

    window.HttpErr = {
        HttpError: HttpError,
        csrf: csrf,
        fetchJSON: fetchJSON,
        describe: describe,
        log: log,
        poll: poll,
        showStale: showStale
    };

    // Pages not migrated yet: every failed jQuery request leaves at least one
    // console line. Logging only; no UI change.
    document.addEventListener('DOMContentLoaded', function () {
        if (window.jQuery) {
            window.jQuery(document).ajaxError(function (event, xhr, settings, thrown) {
                if (thrown === 'abort' || (xhr && xhr.statusText === 'abort')) return;  // deliberate cancel
                log('ajax', xhr, (settings && settings.type || '') + ' ' + (settings && settings.url || ''));
            });
        }
    });
})(window, document);
