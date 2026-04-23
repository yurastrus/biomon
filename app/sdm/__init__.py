"""
SDM Flask blueprint — обгортка для сусіднього репозиторію shared-sdm.

УВАГА: цей файл існує лише поки shared-sdm є СУСІДНІМ репозиторієм.
Коли буде підключено як git submodule (app/sdm/), цей каталог
буде ЗАМІНЕНО вмістом shared-sdm — і цей __init__.py зникне.

Перехід на submodule:
    git rm -r app/sdm
    git submodule add https://github.com/yurastrus/shared-sdm.git app/sdm
    git commit -m "Add shared-sdm as submodule"
"""
import sys
from pathlib import Path

from flask import Blueprint

# Підтримуємо обидва варіанти:
#   1. submodule: app/sdm/  (цей файл тоді — shared-sdm/__init__.py)
#   2. sibling:   ../shared-sdm/
_sdm_candidates = [
    Path(__file__).resolve().parent,                        # submodule (цей файл вже там)
    Path(__file__).resolve().parents[3] / "shared-sdm",    # sibling repo
]
_shared_sdm_root = next(
    (p for p in _sdm_candidates if (p / "adapters").is_dir()),
    None,
)
if _shared_sdm_root and str(_shared_sdm_root) not in sys.path:
    sys.path.insert(0, str(_shared_sdm_root))

sdm_bp = Blueprint(
    "sdm",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/sdm/static",
    url_prefix="/sdm",
)

from . import routes  # noqa: E402, F401
