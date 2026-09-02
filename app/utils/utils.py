# SPDX-License-Identifier: AGPL-3.0-only
from flask import request
from urllib.parse import urlparse


def is_safe_url(target):
    """Return True if the redirect target URL is safe (same host, no open-redirect)."""
    if not target:
        return False
    # Browsers fold backslashes to forward slashes, so "/\evil.com" (which urlparse
    # reads as a bare path) becomes "//evil.com" = an external redirect. Reject any
    # backslash before parsing. Also reject control/whitespace chars that can be
    # used to smuggle a scheme past urlparse.
    if '\\' in target or any(ord(c) < 0x20 for c in target):
        return False
    test_url = urlparse(target)
    return test_url.scheme in ('', 'http', 'https') and \
           (not test_url.netloc or test_url.netloc == request.host)


def build_institution_groups(institutions, lang):
    """Group Institution objects by ecoregion for a checkbox/optgroup list.

    Returns list of dicts:
      {'eco_key': str|None, 'eco_name': str, 'institutions': [...]}
    eco_key is the Ukrainian ecoregion string (used as the stable key in JS).

    Lives here rather than in the admin package because both the admin user form
    and the public registration form render the same grouping.
    """
    from collections import OrderedDict

    eco_map = OrderedDict()
    ungrouped = []

    for inst in institutions:
        if inst.ecoregion_uk:
            if inst.ecoregion_uk not in eco_map:
                display = inst.ecoregion_uk if lang != 'en' else (inst.ecoregion_en or inst.ecoregion_uk)
                eco_map[inst.ecoregion_uk] = {'eco_key': inst.ecoregion_uk,
                                              'eco_name': display,
                                              'institutions': []}
            eco_map[inst.ecoregion_uk]['institutions'].append(inst)
        else:
            ungrouped.append(inst)

    groups = list(eco_map.values())
    if ungrouped:
        ungrouped_label = 'No ecoregion' if lang == 'en' else 'Без екорегіону'
        groups.append({'eco_key': None, 'eco_name': ungrouped_label,
                       'institutions': ungrouped})
    return groups
