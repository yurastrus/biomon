"""
Site-wide error infrastructure (error audit 2026-11-04, error_audit/ERROR_AUDIT.md):
request ids, global JSON/HTML error handlers, CSRF response, log_and_respond,
logging setup, and the shared client helper include in base.html.

Run:
    venv/Scripts/python -m pytest tests/test_error_infra.py -v
"""
import logging

import pytest
from flask import abort, make_response


@pytest.fixture(scope='module')
def err_app(_ct_engine_patch):
    """A separate app with throw-away routes and production-like propagation."""
    from app import create_app
    app = create_app('testing')
    app.config['WTF_CSRF_ENABLED'] = False

    @app.route('/__t/boom')
    def _boom():
        return 1 / 0

    @app.route('/__t/api/boom')
    def _api_boom():
        raise RuntimeError('db exploded')

    @app.route('/__t/bad')
    def _bad():
        abort(400, 'Поле "lat" обовʼязкове')

    @app.route('/__t/forbidden')
    def _forbidden():
        abort(403)

    @app.route('/__t/custom-response')
    def _custom():
        abort(make_response('teapot body', 418))

    @app.route('/__t/only-post', methods=['POST'])
    def _only_post():
        return 'ok'

    @app.route('/__t/slash/')
    def _slash():
        return 'ok'

    @app.route('/__t/lar', methods=['GET', 'POST'])
    def _lar():
        from app.errors import log_and_respond
        try:
            raise ValueError('bad thing')
        except ValueError as e:
            return log_and_respond('t-tag', e, status=500, item=7)

    @app.route('/__t/lar-400')
    def _lar_400():
        from app.errors import log_and_respond
        try:
            raise KeyError('x')
        except KeyError as e:
            return log_and_respond('t-tag', e, status=400,
                                   user_message='Не знайдено поле x')

    return app


@pytest.fixture
def prod_like(err_app):
    """Handlers active (no debugger re-raise), as in production."""
    err_app.debug = False
    err_app.config['PROPAGATE_EXCEPTIONS'] = False
    yield err_app
    err_app.debug = True
    err_app.config['PROPAGATE_EXCEPTIONS'] = None


@pytest.fixture
def ec(prod_like):
    from app.extensions import db
    with prod_like.app_context():
        db.create_all()
        try:
            yield prod_like.test_client()
        finally:
            db.session.rollback()
            db.drop_all()


XHR = {'X-Requested-With': 'XMLHttpRequest'}


# ── request id ─────────────────────────────────────────────────────────────

def test_every_response_carries_request_id(ec):
    r = ec.get('/__t/slash/')
    rid = r.headers.get('X-Request-ID')
    assert rid and len(rid) == 12


def test_valid_incoming_request_id_is_reused(ec):
    r = ec.get('/__t/slash/', headers={'X-Request-ID': 'abcdef0123456789abcdef0123456789'})
    assert r.headers['X-Request-ID'] == 'abcdef0123456789abcdef0123456789'


def test_malformed_incoming_request_id_is_replaced(ec):
    bad = 'evil; DROP <script> [rid=forged]'
    r = ec.get('/__t/slash/', headers={'X-Request-ID': bad})
    assert r.headers['X-Request-ID'] != bad
    assert len(r.headers['X-Request-ID']) == 12


# ── HTTP errors ────────────────────────────────────────────────────────────

def test_404_page_is_translated_html_with_request_id(ec):
    r = ec.get('/uk/definitely-not-here-xyz')
    assert r.status_code == 404
    assert r.mimetype == 'text/html'
    body = r.get_data(as_text=True)
    assert 'Сторінку не знайдено' in body
    assert r.headers['X-Request-ID'] in body
    assert 'noindex' in body


def test_404_page_english(ec):
    r = ec.get('/en/definitely-not-here-xyz')
    assert r.status_code == 404
    assert 'Page not found.' in r.get_data(as_text=True)


def test_404_json_for_api_path(ec):
    r = ec.get('/uk/camera-traps/api/definitely-not-here')
    assert r.status_code == 404
    data = r.get_json()
    assert data['code'] == 'http_404'
    assert data['request_id'] == r.headers['X-Request-ID']
    assert data['error']


def test_json_for_xhr_on_non_api_path(ec):
    r = ec.get('/__t/forbidden', headers=XHR)
    assert r.status_code == 403
    assert r.get_json()['code'] == 'http_403'


def test_custom_abort_description_is_kept(ec):
    r = ec.get('/__t/bad', headers=XHR)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'Поле "lat" обовʼязкове'


def test_405_keeps_allow_header(ec):
    r = ec.get('/__t/only-post', headers=XHR)
    assert r.status_code == 405
    assert 'POST' in r.headers.get('Allow', '')
    assert r.get_json()['code'] == 'http_405'


def test_abort_with_full_response_passes_through(ec):
    r = ec.get('/__t/custom-response')
    assert r.status_code == 418
    assert r.get_data(as_text=True) == 'teapot body'


def test_routing_redirect_not_intercepted(ec):
    r = ec.get('/__t/slash')
    assert r.status_code in (301, 308)


# ── unhandled exceptions ──────────────────────────────────────────────────

def test_unhandled_exception_json_and_traceback_logged(ec, caplog):
    with caplog.at_level(logging.ERROR):
        r = ec.get('/__t/api/boom')
    assert r.status_code == 500
    data = r.get_json()
    assert data['code'] == 'internal_error'
    assert '[RuntimeError]' in data['error']
    assert data['request_id'] in data['error']
    assert 'db exploded' not in data['error']          # no raw internals in UI
    rec = [x for x in caplog.records if '[unhandled]' in x.getMessage()]
    assert rec and rec[0].exc_info is not None          # traceback kept
    assert 'db exploded' in rec[0].getMessage()


def test_unhandled_exception_html_page(ec):
    r = ec.get('/__t/boom')
    assert r.status_code == 500
    assert r.mimetype == 'text/html'
    assert 'ZeroDivisionError' in r.get_data(as_text=True)


def test_error_page_render_failure_falls_back_to_bare_html(ec, monkeypatch):
    import app.errors as errors_mod

    def broken(*a, **k):
        raise RuntimeError('template broke')
    monkeypatch.setattr(errors_mod, 'render_template', broken)
    r = ec.get('/__t/boom')
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert '<h1>500</h1>' in body and r.headers['X-Request-ID'] in body


def test_testing_mode_still_propagates_exceptions(err_app):
    """Default test config (DEBUG/TESTING) keeps exceptions visible to tests."""
    assert err_app.debug is True
    with pytest.raises(ZeroDivisionError):
        err_app.test_client().get('/__t/boom')


# ── CSRF ───────────────────────────────────────────────────────────────────

def test_csrf_failure_xhr_gets_json(ec, prod_like):
    prod_like.config['WTF_CSRF_ENABLED'] = True
    try:
        r = ec.post('/__t/only-post', headers=XHR, data={'a': '1'})
    finally:
        prod_like.config['WTF_CSRF_ENABLED'] = False
    assert r.status_code == 400
    data = r.get_json()
    assert data['code'] == 'csrf_expired'
    assert data['request_id'] == r.headers['X-Request-ID']


def test_csrf_failure_form_post_gets_page(ec, prod_like):
    prod_like.config['WTF_CSRF_ENABLED'] = True
    try:
        r = ec.post('/__t/only-post', data={'a': '1'})
    finally:
        prod_like.config['WTF_CSRF_ENABLED'] = False
    assert r.status_code == 400
    assert r.mimetype == 'text/html'
    assert 'Оновіть сторінку' in r.get_data(as_text=True)


# ── log_and_respond ───────────────────────────────────────────────────────

def test_log_and_respond_json(ec, caplog):
    with caplog.at_level(logging.WARNING):
        r = ec.get('/__t/lar', headers=XHR)
    assert r.status_code == 500
    data = r.get_json()
    assert data['code'] == 't-tag' and '[ValueError]' in data['error']
    rec = [x for x in caplog.records if '[t-tag]' in x.getMessage()]
    assert rec and 'item=7' in rec[0].getMessage() and rec[0].exc_info


def test_log_and_respond_4xx_warning_without_traceback(ec, caplog):
    with caplog.at_level(logging.WARNING):
        r = ec.get('/__t/lar-400', headers=XHR)
    assert r.status_code == 400
    assert r.get_json()['error'] == 'Не знайдено поле x'
    rec = [x for x in caplog.records if '[t-tag]' in x.getMessage()]
    assert rec[0].levelno == logging.WARNING and not rec[0].exc_info


def test_log_and_respond_page_redirects_same_host_only(ec):
    r = ec.post('/__t/lar', headers={'Referer': 'http://localhost/uk/about'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/uk/about')
    r = ec.post('/__t/lar', headers={'Referer': 'https://evil.example/x'})
    assert r.status_code == 302 and 'evil.example' not in r.headers['Location']


# ── logging setup ─────────────────────────────────────────────────────────

def test_request_context_filter_outside_and_inside_request(err_app):
    from app.logging_setup import RequestContextFilter
    f = RequestContextFilter()
    rec = logging.LogRecord('x', logging.INFO, __file__, 1, 'm', None, None)
    assert f.filter(rec) and rec.request_id == '-' and rec.user_id == '-'
    with err_app.test_request_context('/uk/about', method='POST'):
        from flask import g
        g.request_id = 'rid123456789'
        rec = logging.LogRecord('x', logging.INFO, __file__, 1, 'm', None, None)
        f.filter(rec)
        assert rec.request_id == 'rid123456789'
        assert rec.path == 'POST /uk/about'
        assert rec.user_id == '-'          # user not loaded -> no DB hit


def test_configure_logging_idempotent_and_kills_basicconfig(monkeypatch):
    from flask import Flask
    from app.logging_setup import configure_logging, _BiomonHandler
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        app = Flask('x')
        app.config['LOG_LEVEL'] = 'INFO'
        configure_logging(app)
        configure_logging(app)
        ours = [h for h in root.handlers if isinstance(h, _BiomonHandler)]
        assert len(ours) == 1
        assert root.level == logging.INFO
        before = len(root.handlers)
        logging.basicConfig(level=logging.DEBUG)      # what the calculators do
        assert len(root.handlers) == before            # no-op
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_configure_logging_skipped_in_testing():
    from flask import Flask
    from app.logging_setup import configure_logging, _BiomonHandler
    app = Flask('y')
    app.config['TESTING'] = True
    configure_logging(app)
    assert not any(isinstance(h, _BiomonHandler) for h in logging.getLogger().handlers)


# ── client helper include ─────────────────────────────────────────────────

def test_base_includes_shared_error_helper(ec):
    r = ec.get('/uk/login')
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'js/http_errors.js' in body
    assert 'window.HTTP_I18N' in body


def test_shared_error_helper_is_served(ec):
    r = ec.get('/static/js/http_errors.js')
    assert r.status_code == 200
    assert 'window.HttpErr' in r.get_data(as_text=True)
