"""Обгортка DeepFaune (v1.4.x) під наш IClassifier.

DeepFaune-API живе в окремому склонованому репо (за домовленістю —
/opt/biomon-ai/deepfaune/ на сервері). Тут ми додаємо її шлях у sys.path
і імпортуємо PredictorImage.

ВАЖЛИВО — порядок:
    DeepFaune ВНУТРІШНЬО ПЕРЕВПОРЯДКОВУЄ файли за датою EXIF.
    Тож output порядок ≠ input порядок.
    Ми використовуємо predictor.getFilenames() щоб зіставити прогнози
    з шляхами, а повертаємо результат у ВХІДНОМУ порядку
    (worker'у це зручніше).

ВАЖЛИВО — sequence boundary:
    DeepFaune має параметр maxlag (секунди): фото з різницею у часі
    > maxlag вважаються різними послідовностями. У нашому випадку
    worker уже передає фото ОДНІЄЇ observation (тобто це і є одна
    послідовність за визначенням біомону), тож виставляємо
    maxlag=999999, щоб DeepFaune не розбивав їх на під-послідовності.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from typing import List, Optional

from .adapter import IClassifier, PhotoPrediction


logger = logging.getLogger(__name__)


# Дефолтний шлях до DeepFaune на сервері. Override через env DEEPFAUNE_PATH
# або параметр конструктора (для тестів).
_DEFAULT_DEEPFAUNE_PATH = '/opt/biomon-ai/deepfaune'


class DeepFauneAdapter(IClassifier):
    """Класифікатор на основі DeepFaune ViT + детектора yolov8s.

    Параметри:
        deepfaune_path: куди склонована DeepFaune. Default — env DEEPFAUNE_PATH
            або /opt/biomon-ai/deepfaune.
        threshold:    нижче цього score prediction_label стає 'undefined',
                      але top1_label лишається справжнім класом.
        version:      рядок версії, який запишеться в ai_models.version.
                      Default '1.4.1' — оновлюйте коли підтягнете нову версію.
        device:       None → авто-вибір (CPU якщо нема CUDA).
    """

    def __init__(
        self,
        deepfaune_path: Optional[str] = None,
        threshold: float = 0.8,
        version: str = '1.4.1',
        device: Optional[str] = None,
    ):
        self._deepfaune_path = (
            deepfaune_path
            or os.environ.get('DEEPFAUNE_PATH')
            or _DEFAULT_DEEPFAUNE_PATH
        )
        self._threshold = threshold
        self._version = version
        self._device = device

        # Додаємо DeepFaune у sys.path і робимо ledger-імпорт
        if self._deepfaune_path not in sys.path:
            sys.path.insert(0, self._deepfaune_path)
        try:
            from predictTools import PredictorImage
        except ImportError as e:
            raise RuntimeError(
                f"Не вдалось імпортувати predictTools з {self._deepfaune_path}. "
                f"Перевірте що DeepFaune склонована за цим шляхом і всі залежності "
                f"(torch, ultralytics, timm, yolov5) встановлено в активному venv. "
                f"Деталі: {e}"
            ) from e
        self._PredictorImage = PredictorImage

        # Обмежуємо PyTorch CPU-потоки щоб не зловити OOM/перевантаження сервера.
        # Беремо мін(2, CPU_count-1) — лишаємо хоча б 1 ядро для інших процесів.
        # Override через env TORCH_NUM_THREADS якщо треба.
        # NOTE: `os` уже імпортовано на module-level (рядок 28), тому НЕ робимо
        # повторний `import os` у try-блоці — Python вважатиме `os` локальним
        # і впаде на доступі до `os.environ.get('DEEPFAUNE_PATH')` вище.
        try:
            import torch as _torch
            n_threads = int(os.environ.get(
                'TORCH_NUM_THREADS',
                max(1, min(2, (os.cpu_count() or 4) - 1)),
            ))
            _torch.set_num_threads(n_threads)
            logger.info(f"PyTorch num_threads set to {n_threads}")
        except Exception as e:
            logger.warning(f"Failed to set torch threads: {e}")

        logger.info(
            f"DeepFauneAdapter initialized: path={self._deepfaune_path} "
            f"threshold={threshold} version={version}"
        )

    @property
    def name(self) -> str:
        return 'DeepFaune'

    @property
    def version(self) -> str:
        return self._version

    @property
    def config(self) -> dict:
        return {
            'threshold': self._threshold,
            'device': self._device or 'auto',
            'birdclassification': True,     # птахи теж класифікуємо (8 під-класів)
            'maxlag_seconds': 999999,       # вся observation = одна послідовність
            'detector': 'deepfaune-yolov8s_960 + md_v1000.0.0-sorrel',
            'classifier': 'vit_large_patch14_dinov2.lvd142m.v4',
        }

    # ─────────────────────────────────────────────────────────────────
    # Головний метод
    # ─────────────────────────────────────────────────────────────────

    def predict_observation(
        self,
        photo_paths: List[str],
    ) -> List[PhotoPrediction]:
        if not photo_paths:
            return []

        # Конвертуємо у звичайні рядки (PredictorImage очікує list[str])
        paths_str = [str(p) for p in photo_paths]

        predictor = self._PredictorImage(
            paths_str,
            self._threshold,
            999999,           # maxlag → форсуємо single-sequence для observation
            'en',             # мова labels
            True,             # birdclassification увімкнено (bird-head ваги)
        )
        predictor.allBatch()

        # DeepFaune перевпорядковує файли за EXIF date → беремо її порядок
        ordered_paths = predictor.getFilenames()
        pred, score, boxes, count   = predictor.getPredictions()       # sequence-aware
        pred_b, score_b, _, _       = predictor.getPredictionsBase()   # per-photo
        top1                        = predictor.getPredictedTop1()     # без threshold
        human_count                 = predictor.getHumanCount()

        # Будуємо словник path → PhotoPrediction
        by_path: dict[str, PhotoPrediction] = {}
        for i, path in enumerate(ordered_paths):
            by_path[path] = PhotoPrediction(
                photo_path=path,
                prediction_label=_clean_label(pred[i]),
                prediction_score=_safe_float(score[i]),
                base_label=_clean_label(pred_b[i]),
                base_score=_safe_float(score_b[i]),
                # top1 не має окремого score — використовуємо той самий що для base
                top1_label=_clean_label(top1[i]),
                top1_score=_safe_float(score_b[i]),
                animal_count=_safe_int(count[i] if hasattr(count, '__getitem__') else None),
                human_count=_safe_int(human_count[i] if i < len(human_count) else None),
                bbox=_bbox_to_dict(boxes[i] if i < len(boxes) else None),
            )

        # Повертаємо у ВХІДНОМУ порядку. Якщо DeepFaune нормалізував шлях
        # (наприклад, форвард- vs бек-слеши на Windows) — fallback по basename.
        out: List[PhotoPrediction] = []
        for p in paths_str:
            if p in by_path:
                out.append(by_path[p])
                continue
            # Fallback: пошук по basename (Windows / vs \)
            base = os.path.basename(p)
            match = next(
                (rp for rp in by_path if os.path.basename(rp) == base),
                None,
            )
            if match:
                # повертаємо ту ж саму "сутність", але з оригінальним шляхом
                # (worker мапить через photo_id_by_path[input_path])
                pred_match = by_path[match]
                out.append(PhotoPrediction(
                    photo_path=p,
                    prediction_label=pred_match.prediction_label,
                    prediction_score=pred_match.prediction_score,
                    base_label=pred_match.base_label,
                    base_score=pred_match.base_score,
                    top1_label=pred_match.top1_label,
                    top1_score=pred_match.top1_score,
                    animal_count=pred_match.animal_count,
                    human_count=pred_match.human_count,
                    bbox=pred_match.bbox,
                ))
            else:
                logger.warning(
                    f"DeepFaune не повернула прогноз для {p}. "
                    f"Output paths: {list(by_path.keys())[:3]}..."
                )
                out.append(PhotoPrediction(
                    photo_path=p,
                    error='DeepFaune did not return a prediction for this path',
                ))
        return out


# ─────────────────────────────────────────────────────────────────────
# Helper-конвертації
# ─────────────────────────────────────────────────────────────────────

def _clean_label(value) -> Optional[str]:
    """Нормалізує label від DeepFaune.

    DeepFaune повертає:
      - '' (пустий рядок) коли прогноз ще не готовий — повертаємо None.
      - 'undefined' коли score < threshold — повертаємо як є, бо це валідне
        значення для prediction_label (а species_map.py змапить його на None).
      - реальний клас 'roe deer', 'fox', ... — повертаємо в lower-case.
    """
    if value is None or value == '':
        return None
    return str(value).strip().lower()


def _safe_float(value) -> Optional[float]:
    """Float з захистом від None/NaN."""
    if value is None:
        return None
    try:
        v = float(value)
        if math.isnan(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> Optional[int]:
    """Int з захистом від None/NaN."""
    if value is None:
        return None
    try:
        v = int(value)
        return v
    except (TypeError, ValueError):
        return None


def _bbox_to_dict(bbox) -> Optional[dict]:
    """Конвертує numpy-array bbox у звичайний dict для JSONB.

    DeepFaune повертає bbox як [x1, y1, x2, y2] у нормалізованих координатах
    (0..1 від розмірів зображення).
    """
    if bbox is None:
        return None
    try:
        coords = list(bbox)
        if len(coords) < 4:
            return None
        x1, y1, x2, y2 = coords[:4]
        return {
            'x1': _safe_float(x1),
            'y1': _safe_float(y1),
            'x2': _safe_float(x2),
            'y2': _safe_float(y2),
        }
    except (TypeError, ValueError):
        return None
