"""
SEC-002 (#24): SECRET_KEY fail-fast -- no fallback to a known key.

config.py must take SECRET_KEY purely from the environment (os.environ['SECRET_KEY']),
with no hardcoded stub. If the key is missing -- KeyError on import (an explicit
crash is better than a silent start with a known key).

The test deliberately does NOT do pop+reload (config.py calls load_dotenv(), which
would pull SECRET_KEY back from .env -- the KeyError wouldn't reproduce reliably on
a machine with a .env). Instead we check the source + the value.

Run:
    venv/Scripts/python -m pytest tests/test_secret_key.py -v
"""
import re
from pathlib import Path

CONFIG_SRC = (Path(__file__).resolve().parent.parent / 'config.py').read_text(encoding='utf-8')

OLD_FALLBACK = 'a-very-secret-string-for-development'


def test_no_hardcoded_fallback_in_source():
    """No hardcoded fallback key remains in config.py."""
    assert OLD_FALLBACK not in CONFIG_SRC, (
        "config.py досі містить fallback SECRET_KEY — fail-fast не діє")


def test_secret_key_read_from_environ_directly():
    """SECRET_KEY is taken as os.environ['SECRET_KEY'] (fail-fast),
    not .get(...) with a fallback."""
    assert re.search(r"SECRET_KEY\s*=\s*os\.environ\[\s*['\"]SECRET_KEY['\"]\s*\]",
                     CONFIG_SRC), "SECRET_KEY має читатись через os.environ[...] (fail-fast)"
    # the old .get(...) or '...' pattern must be gone
    assert not re.search(r"SECRET_KEY\s*=\s*os\.environ\.get\(\s*['\"]SECRET_KEY['\"]\s*\)\s*or",
                         CONFIG_SRC)


def test_loaded_config_secret_not_default(app):
    """In the loaded app SECRET_KEY does not equal the old stub
    and is long enough."""
    key = app.config['SECRET_KEY']
    assert key
    assert key != OLD_FALLBACK
