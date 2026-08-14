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
