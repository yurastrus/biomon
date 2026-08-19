# SPDX-License-Identifier: AGPL-3.0-only
"""Signed, expiring tokens for email confirmation links.

No token is stored in the database: the signature is the proof. The payload is
the address the link was sent to, so a token cannot be replayed against an
account whose email has since changed, and a leaked token grants nothing beyond
confirming that one address.
"""
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app

#: Namespaces the signature — a token minted for one purpose cannot be
#: replayed for another even though both use SECRET_KEY.
EMAIL_CONFIRM_SALT = 'biomon-email-confirm'


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def generate_email_token(email):
    """Return a signed token carrying ``email``."""
    return _serializer().dumps(email, salt=EMAIL_CONFIRM_SALT)


def verify_email_token(token, max_age=None):
    """Return the email carried by ``token``, or None if invalid/expired.

    Args:
        token: the value taken from the confirmation URL.
        max_age: seconds the token stays valid; defaults to
            ``EMAIL_CONFIRM_TOKEN_MAX_AGE`` from config (24 h).
    """
    if max_age is None:
        max_age = current_app.config.get('EMAIL_CONFIRM_TOKEN_MAX_AGE', 86400)
    try:
        return _serializer().loads(token, salt=EMAIL_CONFIRM_SALT, max_age=max_age)
    except SignatureExpired:
        current_app.logger.info("Email confirmation token expired")
        return None
    except BadSignature:
        current_app.logger.warning("Email confirmation token has a bad signature")
        return None
