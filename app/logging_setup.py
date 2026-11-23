# SPDX-License-Identifier: AGPL-3.0-only
"""One logging configuration for the whole app (web workers and CLI workers).

Why this exists (error audit 2026-11-04, error_audit/ERROR_AUDIT.md §1):
logging used to be unconfigured, so production ran at Python's default
WARNING level and every ``logger.info`` audit line was dropped. Two analytics
calculators call ``logging.basicConfig(INFO)`` at import time, which switched
the level and duplicated lines differently in each gunicorn worker. Installing
one root handler here, first thing in ``create_app``, turns those later
``basicConfig`` calls into no-ops (``basicConfig`` does nothing once the root
logger has a handler).

Every record gets the request context, so a line looks like::

    2026-11-04 12:00:00,123 ERROR app [rid=3f2a9c1b7d4e uid=17 ip=1.2.3.4 POST /uk/...] [ct-upload] ...

and a user's screenshot with the request id maps to one
``journalctl -u biomon | grep <rid>``.
"""
import logging
import sys

from flask import g, has_request_context, request

LOG_FORMAT = ('%(asctime)s %(levelname)s %(name)s '
              '[rid=%(request_id)s uid=%(user_id)s ip=%(ip)s %(path)s] %(message)s')

# Third-party loggers that are chatty at INFO.
_NOISY_LOGGERS = ('werkzeug', 'urllib3', 'googleapiclient', 'google', 'PIL',
                  'matplotlib', 'fiona', 'rasterio')


class RequestContextFilter(logging.Filter):
    """Attach request id, user id, client IP and method+path to each record.

    Never triggers a DB query: the user id is read only if Flask-Login has
    already loaded the user for this request (``g._login_user``). Logging must
    keep working when the DB session is broken, which is exactly when we log.
    """

    def filter(self, record):
        record.request_id = record.user_id = record.ip = record.path = '-'
        if not has_request_context():
            return True
        try:
            record.request_id = getattr(g, 'request_id', '-') or '-'
            user = getattr(g, '_login_user', None)
            if user is not None and getattr(user, 'is_authenticated', False):
                record.user_id = user.get_id() or '-'
            record.ip = request.remote_addr or '-'
            record.path = f'{request.method} {request.path}'
        except Exception:  # never let the log line itself fail
            pass
        return True


class _BiomonHandler(logging.StreamHandler):
    """Marker subclass so repeated create_app() calls don't stack handlers."""


def configure_logging(app):
    """Install the root handler. Idempotent. Skipped under TESTING.

    Tests rely on pytest's own capture handlers on the root logger, which a
    reconfiguration here would remove; the filter and format are unit-tested
    directly instead.
    """
    if app.config.get('TESTING'):
        return

    level_name = str(app.config.get('LOG_LEVEL', 'INFO')).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    if not any(isinstance(h, _BiomonHandler) for h in root.handlers):
        handler = _BiomonHandler(sys.stderr)  # gunicorn -> systemd -> journald
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(RequestContextFilter())
        root.addHandler(handler)
    root.setLevel(level)

    # Flask's default handler on app.logger would print every app line a
    # second time (once there, once via propagation to root).
    from flask.logging import default_handler
    app.logger.removeHandler(default_handler)
    app.logger.setLevel(logging.NOTSET)  # inherit the root level
    app.logger.propagate = True

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
