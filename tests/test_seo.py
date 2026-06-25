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
