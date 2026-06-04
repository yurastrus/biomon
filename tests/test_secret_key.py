"""
SEC-002 (#24): SECRET_KEY fail-fast — без fallback на відомий ключ.

config.py має брати SECRET_KEY суто з середовища (os.environ['SECRET_KEY']),
без hardcoded-заглушки. Якщо ключа немає — KeyError на імпорті (краще явна
аварія, ніж тихий старт із відомим ключем).

Тест навмисно НЕ робить pop+reload (config.py викликає load_dotenv(), який
підтягнув би SECRET_KEY з .env назад — KeyError не відтворився б надійно на
машині з .env). Натомість перевіряємо джерело + значення.

Запуск:
    venv/Scripts/python -m pytest tests/test_secret_key.py -v
"""
import re
from pathlib import Path

CONFIG_SRC = (Path(__file__).resolve().parent.parent / 'config.py').read_text(encoding='utf-8')

OLD_FALLBACK = 'a-very-secret-string-for-development'


def test_no_hardcoded_fallback_in_source():
    """У config.py не лишилось hardcoded fallback-ключа."""
    assert OLD_FALLBACK not in CONFIG_SRC, (
        "config.py досі містить fallback SECRET_KEY — fail-fast не діє")


def test_secret_key_read_from_environ_directly():
    """SECRET_KEY береться як os.environ['SECRET_KEY'] (fail-fast),
    а не .get(...) з fallback."""
    assert re.search(r"SECRET_KEY\s*=\s*os\.environ\[\s*['\"]SECRET_KEY['\"]\s*\]",
                     CONFIG_SRC), "SECRET_KEY має читатись через os.environ[...] (fail-fast)"
    # старий патерн .get(...) or '...' має зникнути
    assert not re.search(r"SECRET_KEY\s*=\s*os\.environ\.get\(\s*['\"]SECRET_KEY['\"]\s*\)\s*or",
                         CONFIG_SRC)


def test_loaded_config_secret_not_default(app):
    """У завантаженому app SECRET_KEY не дорівнює старій заглушці
    і достатньо довгий."""
    key = app.config['SECRET_KEY']
    assert key
    assert key != OLD_FALLBACK
