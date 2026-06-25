# SPDX-License-Identifier: AGPL-3.0-only
"""SEO infrastructure: dynamic /robots.txt and /sitemap.xml.

Registered at the domain root (NO /<lang_code> prefix) — search engines only
read these from the site root (biomon.app/robots.txt, biomon.app/sitemap.xml).

Both routes are dynamic so that absolute URLs (the `Sitemap:` line in robots.txt
and every <loc>/<xhtml:link> in the sitemap) carry the real host from the
request context, instead of a hard-coded domain.
"""
from datetime import date

from flask import Blueprint, Response, current_app, render_template, url_for

seo_bp = Blueprint('seo', __name__)

# Public, indexable pages → one <url> per language, with full hreflang clusters.
# (endpoint, priority). All take a single `lang_code` view arg.
PUBLIC_ENDPOINTS = [
    ('main.index', '1.0'),
    ('main.about', '0.8'),
    ('main.contacts', '0.8'),
    ('pam.pam_home', '0.8'),
    ('camera_traps.overview', '0.8'),
]


@seo_bp.route('/robots.txt')
def robots_txt():
    """Dynamic robots.txt with an absolute Sitemap: URL (Google requirement)."""
    # Absolute URL with scheme + host taken from the request context.
    sitemap_url = url_for('seo.sitemap', _external=True)

    lines = [
        'User-agent: *',
        'Allow: /',
        # Each Disallow on its own line (multiple paths per line is invalid).
        'Disallow: /*/admin',
        'Disallow: /*/sdm',
        'Disallow: /*/profile',
        'Disallow: /*/logout',
        'Disallow: /csp-report',
        # NOTE: /<lang>/login is deliberately NOT blocked here — it carries a
        # `noindex` meta instead. Blocking it would stop crawlers from seeing
        # that meta, and the bare URL could still get indexed.
        '',
        f'Sitemap: {sitemap_url}',
        '',
    ]
    return Response('\n'.join(lines), mimetype='text/plain')


@seo_bp.route('/sitemap.xml')
def sitemap():
    """Dynamic sitemap with reciprocal hreflang alternates + x-default."""
    languages = list(current_app.config['LANGUAGES'].keys())
    default_lang = current_app.config.get('BABEL_DEFAULT_LOCALE', 'uk')
    lastmod = date.today().isoformat()

    pages = []
    for endpoint, priority in PUBLIC_ENDPOINTS:
        # Build the full alternate cluster once per page: every language version
        # (including itself) + x-default → the default-locale version. Each
        # <url> must repeat the complete set, else Google ignores the cluster.
        alternates = [
            {'hreflang': code,
             'href': url_for(endpoint, lang_code=code, _external=True)}
            for code in languages
        ]
        alternates.append({
            'hreflang': 'x-default',
            'href': url_for(endpoint, lang_code=default_lang, _external=True),
        })

        for code in languages:
            pages.append({
                'loc': url_for(endpoint, lang_code=code, _external=True),
                'lastmod': lastmod,
                'changefreq': 'weekly',
                'priority': priority,
                'alternates': alternates,
            })

    xml = render_template('seo/sitemap.xml.j2', pages=pages)
    return Response(xml, mimetype='application/xml')
