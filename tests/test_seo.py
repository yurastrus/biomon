"""Tests for the SEO infrastructure: robots.txt, sitemap.xml, and <head> meta.

Style mirrors tests/test_security_headers.py — uses the shared `client`
fixture from conftest.py (TESTING=True, Talisman skipped).
"""
import xml.etree.ElementTree as ET

import pytest


# ── /robots.txt ──────────────────────────────────────────────────────────────

def test_robots_txt_ok_and_plaintext(client):
    resp = client.get('/robots.txt')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/plain'


def test_robots_txt_has_absolute_sitemap_and_disallows(client):
    body = client.get('/robots.txt').get_data(as_text=True)
    # Sitemap line must be an absolute URL (scheme + host).
    assert 'Sitemap: http://' in body
    assert '/sitemap.xml' in body
    assert 'Disallow: /*/admin' in body
    assert 'Disallow: /*/sdm' in body
    assert 'Disallow: /csp-report' in body
    # login is deliberately NOT disallowed (it carries noindex instead).
    assert 'login' not in body


# ── /sitemap.xml ─────────────────────────────────────────────────────────────

SITEMAP_NS = {
    'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9',
    'xhtml': 'http://www.w3.org/1999/xhtml',
}


def test_sitemap_ok_and_xml(client):
    resp = client.get('/sitemap.xml')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/xml'


def test_sitemap_is_valid_xml_with_both_home_langs(client):
    body = client.get('/sitemap.xml').get_data(as_text=True)
    root = ET.fromstring(body)  # raises if malformed
    locs = [el.text for el in root.iter('{%s}loc' % SITEMAP_NS['sm'])]
    assert any(u.endswith('/uk/') for u in locs)
    assert any(u.endswith('/en/') for u in locs)


def test_sitemap_has_hreflang_alternates_with_xdefault(client):
    body = client.get('/sitemap.xml').get_data(as_text=True)
    root = ET.fromstring(body)
    links = list(root.iter('{%s}link' % SITEMAP_NS['xhtml']))
    assert links, 'expected at least one xhtml:link alternate'
    hreflangs = {el.get('hreflang') for el in links}
    assert 'uk' in hreflangs and 'en' in hreflangs
    assert 'x-default' in hreflangs


def test_sitemap_excludes_private_and_login_urls(client):
    body = client.get('/sitemap.xml').get_data(as_text=True)
    assert '/admin' not in body
    assert '/sdm' not in body
    assert '/login' not in body
    assert '/profile' not in body


def test_sitemap_all_locs_return_200(client):
    body = client.get('/sitemap.xml').get_data(as_text=True)
    root = ET.fromstring(body)
    locs = [el.text for el in root.iter('{%s}loc' % SITEMAP_NS['sm'])]
    assert locs
    for loc in locs:
        # Strip scheme+host → request the path through the test client.
        path = loc.split('localhost', 1)[-1]
        r = client.get(path)
        assert r.status_code == 200, f'{path} returned {r.status_code}'


# ── <head> meta on public pages ──────────────────────────────────────────────

def test_home_head_has_seo_meta(client):
    html = client.get('/uk/').get_data(as_text=True)
    assert '<link rel="canonical"' in html
    assert 'property="og:title"' in html
    assert 'hreflang="x-default"' in html
    assert 'name="description"' in html
    assert 'application/ld+json' in html


def test_home_robots_is_index(client):
    html = client.get('/uk/').get_data(as_text=True)
    assert 'name="robots" content="index, follow"' in html


def test_login_is_noindex(client):
    html = client.get('/uk/login').get_data(as_text=True)
    assert 'noindex' in html


# ── Crawl control: X-Robots-Tag on tooling pages ─────────────────────────────
# The camera_traps / pam templates are public submodules shared with
# yurastrus.dev, so noindex is set from this repo as an HTTP header instead.
# See C:/Temp/seo-coverage-audit-2026-08-17.md, finding F6.

def test_ct_landing_is_indexable(client):
    resp = client.get('/uk/camera-traps/')
    assert resp.status_code == 200
    assert 'noindex' not in resp.headers.get('X-Robots-Tag', '')


def test_pam_landing_is_indexable(client):
    resp = client.get('/uk/pam')
    assert resp.status_code == 200
    assert 'noindex' not in resp.headers.get('X-Robots-Tag', '')


def test_landing_with_query_string_is_noindex(client):
    """Any ?param variant of a landing is crawl noise, not a distinct page."""
    resp = client.get('/uk/camera-traps/?species_id=1')
    assert resp.headers.get('X-Robots-Tag') == 'noindex, follow'


@pytest.mark.parametrize('path', [
    '/uk/camera-traps/dashboard',
    '/uk/camera-traps/gallery',
    '/uk/pam/pam_overview',
])
def test_dashboards_are_noindex(client, path):
    resp = client.get(path)
    assert resp.headers.get('X-Robots-Tag') == 'noindex, follow'


def test_own_pages_keep_no_x_robots_header(client):
    """main.* is our own content — the header must not leak onto it."""
    resp = client.get('/uk/')
    assert resp.status_code == 200
    assert 'X-Robots-Tag' not in resp.headers


def test_indexable_endpoints_matches_sitemap():
    """Regression: the header allowlist and the sitemap come from one list."""
    from app.seo import INDEXABLE_ENDPOINTS, PUBLIC_ENDPOINTS

    assert INDEXABLE_ENDPOINTS == {ep for ep, _ in PUBLIC_ENDPOINTS}
    assert 'camera_traps.overview' in INDEXABLE_ENDPOINTS
    assert 'pam.pam_home' in INDEXABLE_ENDPOINTS


# ── robots.txt hardening ─────────────────────────────────────────────────────

@pytest.mark.parametrize('rule', [
    'Disallow: /*/api/',
    'Disallow: /*/camera-traps/api/',
    'Disallow: /thumbnails/',
    'Disallow: /photos/raw/',
    'Disallow: /*/camera-traps/upload',
    'Disallow: /*?',
])
def test_robots_txt_blocks_crawl_sinks(client, rule):
    active = [
        ln for ln in client.get('/robots.txt').get_data(as_text=True).splitlines()
        if ln.startswith('Disallow:')
    ]
    assert rule in active


def test_robots_txt_keeps_dashboards_crawlable_for_now(client):
    """Stage 2 stays off: blocking a URL hides its noindex header, so the
    dashboards must stay reachable until Search Console shows them dropping."""
    active = [
        ln for ln in client.get('/robots.txt').get_data(as_text=True).splitlines()
        if ln.startswith('Disallow:')
    ]
    assert 'Disallow: /*/camera-traps/dashboard' not in active
    assert 'Disallow: /*/pam/pam_overview' not in active


# ── Canonical host ───────────────────────────────────────────────────────────

def test_www_host_redirects_to_apex(client):
    resp = client.get('/uk/', base_url='http://www.example.com')
    assert resp.status_code == 301
    assert resp.headers['Location'] == 'http://example.com/uk/'


def test_apex_host_is_served_normally(client):
    resp = client.get('/uk/', base_url='http://example.com')
    assert resp.status_code == 200
