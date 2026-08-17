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

# Single source of truth for "may be indexed". `app/__init__.py` imports this to
# decide which CT/PAM/SDM responses get an `X-Robots-Tag: noindex, follow`
# header — keeping the sitemap and the header from ever disagreeing.
INDEXABLE_ENDPOINTS = frozenset(endpoint for endpoint, _priority in PUBLIC_ENDPOINTS)


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
        # JSON APIs — never indexable, and the biggest crawl-budget sink.
        'Disallow: /*/api/',
        'Disallow: /*/camera-traps/api/',
        # Media and static trees served by the app.
        'Disallow: /ct-static/',
        'Disallow: /*/pam-static/',
        'Disallow: /thumbnails/',
        'Disallow: /photos/raw/',
        'Disallow: /*/audio/',
        # Auth-walled trees: they 302 to the sign-in page, which is how the
        # sibling property (yurastrus.dev) accumulated 691 "page with redirect".
        'Disallow: /*/camera-traps/upload',
        'Disallow: /*/camera-traps/import-classification',
        'Disallow: /*/camera-traps/manage-',
        'Disallow: /*/camera-traps/identify',
        'Disallow: /*/camera-traps/data-export',
        'Disallow: /*/pam/verification/',
        'Disallow: /*/pam/manage-',
        'Disallow: /*/pam/data-export',
        # Parameterised dashboards: an effectively infinite URL space. They also
        # carry X-Robots-Tag: noindex (app/__init__.py) for anything already
        # known to Google; this stops new crawling of the filter combinations.
        'Disallow: /*?',
        '',
        '# Stage 2 — do NOT enable yet. The clean dashboard URLs are still in',
        '# the index; blocking them here would hide the noindex header and',
        '# freeze them there. Enable once Search Console shows them dropping.',
        '# Disallow: /*/camera-traps/dashboard',
        '# Disallow: /*/camera-traps/analysis/',
        '# Disallow: /*/camera-traps/gallery',
        '# Disallow: /*/pam/pam_overview',
        '# Disallow: /*/pam/pam_detailed',
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
