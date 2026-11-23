"""Local UI harness: site-wide error infrastructure + window.HttpErr in a real browser.

SQLite main DB, CT engine mocked: touches no real database. Serves on
http://127.0.0.1:5082. Open /__t/page: a page extending base.html runs
window.HttpErr against real error responses of this app (404 JSON, 500 JSON,
CSRF failure, login redirect, network failure, jQuery ajaxError logging) and
prints PASS/FAIL lines into #out (and the console). Production-like error
handling: debug off, exceptions not propagated, CSRF on.

    venv/Scripts/python scripts/error_infra_ui_harness.py
"""
import os
import sys
from unittest.mock import MagicMock, patch

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ.setdefault('SECRET_KEY', 'harness')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

patch('app.camera_traps.database.create_engine', return_value=MagicMock()).start()

from flask import redirect, render_template_string  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

app = create_app('testing')
assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'), 'refusing: not SQLite'
app.debug = False
app.config['DEBUG'] = False
app.config['PROPAGATE_EXCEPTIONS'] = False
app.config['WTF_CSRF_ENABLED'] = True

with app.app_context():
    db.create_all()

PAGE = r'''{% extends "base.html" %}
{% block content %}<div class="container"><h1>HttpErr harness</h1><pre id="out">running…</pre></div>{% endblock %}
{% block scripts %}
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script>
(async function () {
  const R = []; const ok = (c, n) => R.push((c ? 'PASS ' : 'FAIL ') + n);
  const H = window.HttpErr;
  ok(!!H, 'HttpErr loaded from base.html');
  ok(H.csrf().length > 10, 'csrf from meta');
  async function expectErr(p) { try { await p; return null; } catch (e) { return e; } }

  let e = await expectErr(H.fetchJSON('/uk/camera-traps/api/definitely-missing'));
  ok(e && e.status === 404 && e.body.code === 'http_404', '404 JSON -> HttpError ' + (e && H.describe(e)));

  e = await expectErr(H.fetchJSON('/__t/api/boom'));
  ok(e && e.status === 500 && /RuntimeError/.test(e.message) && e.requestId, '500 JSON: ' + (e && H.describe(e)));
  ok(e && H.describe(e).includes(e.requestId), '500 message carries request id');

  e = await expectErr(H.fetchJSON('/__t/only-post', {method: 'POST', headers: {'X-CSRFToken': 'bogus'}, body: {a: 1}}));
  ok(e && e.status === 400 && e.stale === true, 'CSRF failure -> stale: ' + (e && H.describe(e)));

  const good = await H.fetchJSON('/__t/only-post', {method: 'POST', body: {a: 1}});
  ok(good && good.ok === true, 'valid CSRF POST passes');

  e = await expectErr(H.fetchJSON('/__t/to-login'));
  ok(e && e.stale === true, 'login redirect -> stale');

  e = await expectErr(H.fetchJSON('http://127.0.0.1:1/unreachable'));
  ok(e && H.describe(e).startsWith(window.HTTP_I18N.noConn), 'network failure: ' + (e && H.describe(e)));

  // jQuery: un-migrated pages get a console line via the global ajaxError hook.
  const orig = console.error; let logged = '';
  console.error = function () { logged += Array.from(arguments).join(' ') + '\n'; orig.apply(console, arguments); };
  await new Promise(res => $.ajax({url: '/__t/api/boom', complete: () => res()}));
  console.error = orig;
  ok(/\[biomon\]\[ajax\] HTTP=500/.test(logged), 'jQuery ajaxError logged');
  // jqXHR is a thenable: wrap it so the promise doesn't adopt its rejection.
  e = (await new Promise(res => $.ajax({url: '/__t/api/boom', error: x => res({x})}))).x;
  ok(/\[HTTP 500\]/.test(H.describe(e)), 'describe(jqXHR): ' + H.describe(e));
  e = (await new Promise(res => $.ajax({url: '/uk/definitely-missing-page', error: x => res({x})}))).x;
  ok(e.status === 404 && e.responseJSON && e.responseJSON.code === 'http_404',
     'jQuery XHR on a page URL gets JSON 404: ' + H.describe(e));
  const html = await fetch('/__t/html-boom').then(r => r.text().then(t => ({s: r.status, t})));
  ok(html.s === 500 && /ZeroDivisionError/.test(html.t) && /<h1>500<\/h1>/.test(html.t), 'plain browser 500 gets the error page');

  document.getElementById('out').textContent = R.join('\n');
  console.log('[harness] ' + R.filter(x => x.startsWith('FAIL')).length + ' failures');
})();
</script>
{% endblock %}'''


@app.route('/__t/page')
def _page():
    return render_template_string(PAGE)


@app.route('/__t/api/boom')
def _boom():
    raise RuntimeError('simulated DB failure')


@app.route('/__t/only-post', methods=['POST'])
def _only_post():
    return {'ok': True}


@app.route('/__t/to-login')
def _to_login():
    return redirect('/uk/login?next=/__t/to-login')


@app.route('/__t/html-boom')
def _html_boom():
    return 1 / 0


if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', 5082)), debug=False, use_reloader=False)
