from flask import request
from urllib.parse import urlparse


def is_safe_url(target):
    """Return True if the redirect target URL is safe (same host, no open-redirect)."""
    if not target: 
        return False
    test_url = urlparse(target)
    return test_url.scheme in ('', 'http', 'https') and \
           (not test_url.netloc or test_url.netloc == request.host)
