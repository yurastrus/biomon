# SPDX-License-Identifier: AGPL-3.0-only
"""Wrapper around DeepFaune (v1.4.x) for our IClassifier.

The DeepFaune API lives in a separate cloned repo (by convention,
/opt/biomon-ai/deepfaune/ on the server). Here we add its path to sys.path
and import PredictorImage.

IMPORTANT — ordering:
    DeepFaune INTERNALLY REORDERS files by EXIF date.
    So the output order ≠ the input order.
    We use predictor.getFilenames() to match predictions to paths, and
    return the result in the INPUT order (more convenient for the worker).

IMPORTANT — sequence boundary:
    DeepFaune has a maxlag parameter (seconds): photos more than maxlag
    apart in time are treated as different sequences. In our case the
    worker already passes photos of a SINGLE observation (which is one
    sequence by the biomon definition), so we set maxlag=999999 to keep
    DeepFaune from splitting them into sub-sequences.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from typing import List, Optional

from .adapter import IClassifier, PhotoPrediction


logger = logging.getLogger(__name__)


# Default path to DeepFaune on the server. Override via env DEEPFAUNE_PATH
# or the constructor parameter (for tests).
_DEFAULT_DEEPFAUNE_PATH = '/opt/biomon-ai/deepfaune'


class DeepFauneAdapter(IClassifier):
    """Classifier based on DeepFaune ViT + the yolov8s detector.

    Parameters:
        deepfaune_path: where DeepFaune is cloned. Default — env DEEPFAUNE_PATH
            or /opt/biomon-ai/deepfaune.
        threshold:    below this score prediction_label becomes 'undefined',
                      but top1_label stays the real class.
        version:      version string written to ai_models.version.
                      Default '1.4.1' — update when you pull a new version.
        device:       None → auto-select (CPU if there is no CUDA).
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

        # Add DeepFaune to sys.path and do the lazy import
        if self._deepfaune_path not in sys.path:
            sys.path.insert(0, self._deepfaune_path)
        try:
            from predictTools import PredictorImage
        except ImportError as e:
            raise RuntimeError(
                f"Failed to import predictTools from {self._deepfaune_path}. "
                f"Check that DeepFaune is cloned at this path and all dependencies "
                f"(torch, ultralytics, timm, yolov5) are installed in the active venv. "
                f"Details: {e}"
            ) from e
        self._PredictorImage = PredictorImage

        # Limit PyTorch CPU threads to avoid OOM / overloading the server.
        # Take min(2, CPU_count-1) — leave at least 1 core for other processes.
        # Override via env TORCH_NUM_THREADS if needed.
        # NOTE: `os` is already imported at module level (line 28), so do NOT
        # re-`import os` in the try block — Python would treat `os` as local
        # and fail on the `os.environ.get('DEEPFAUNE_PATH')` access above.
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
            'birdclassification': True,     # we also classify birds (8 sub-classes)
            'maxlag_seconds': 999999,       # the whole observation = one sequence
            'detector': 'deepfaune-yolov8s_960 + md_v1000.0.0-sorrel',
            'classifier': 'vit_large_patch14_dinov2.lvd142m.v4',
        }

    # ─────────────────────────────────────────────────────────────────
    # Main method
    # ─────────────────────────────────────────────────────────────────

    def predict_observation(
        self,
        photo_paths: List[str],
    ) -> List[PhotoPrediction]:
        if not photo_paths:
            return []

        # Convert to plain strings (PredictorImage expects list[str])
        paths_str = [str(p) for p in photo_paths]

        predictor = self._PredictorImage(
            paths_str,
            self._threshold,
            999999,           # maxlag → force a single sequence for the observation
            'en',             # label language
            True,             # birdclassification enabled (bird-head weights)
        )
        predictor.allBatch()

        # DeepFaune reorders files by EXIF date → take its order
        ordered_paths = predictor.getFilenames()
        pred, score, boxes, count   = predictor.getPredictions()       # sequence-aware
        pred_b, score_b, _, _       = predictor.getPredictionsBase()   # per-photo
        top1                        = predictor.getPredictedTop1()     # without threshold
        human_count                 = predictor.getHumanCount()

        # Build the path → PhotoPrediction dict
        by_path: dict[str, PhotoPrediction] = {}
        for i, path in enumerate(ordered_paths):
            by_path[path] = PhotoPrediction(
                photo_path=path,
                prediction_label=_clean_label(pred[i]),
                prediction_score=_safe_float(score[i]),
                base_label=_clean_label(pred_b[i]),
                base_score=_safe_float(score_b[i]),
                # top1 has no separate score — reuse the one for base
                top1_label=_clean_label(top1[i]),
                top1_score=_safe_float(score_b[i]),
                animal_count=_safe_int(count[i] if hasattr(count, '__getitem__') else None),
                human_count=_safe_int(human_count[i] if i < len(human_count) else None),
                bbox=_bbox_to_dict(boxes[i] if i < len(boxes) else None),
            )

        # Return in the INPUT order. If DeepFaune normalized the path
        # (e.g. forward- vs back-slashes on Windows) — fall back by basename.
        out: List[PhotoPrediction] = []
        for p in paths_str:
            if p in by_path:
                out.append(by_path[p])
                continue
            # Fallback: look up by basename (Windows / vs \)
            base = os.path.basename(p)
            match = next(
                (rp for rp in by_path if os.path.basename(rp) == base),
                None,
            )
            if match:
                # return the same "entity", but with the original path
                # (the worker maps via photo_id_by_path[input_path])
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
                    f"DeepFaune returned no prediction for {p}. "
                    f"Output paths: {list(by_path.keys())[:3]}..."
                )
                out.append(PhotoPrediction(
                    photo_path=p,
                    error='DeepFaune did not return a prediction for this path',
                ))
        return out


# ─────────────────────────────────────────────────────────────────────
# Helper conversions
# ─────────────────────────────────────────────────────────────────────

def _clean_label(value) -> Optional[str]:
    """Normalize a label from DeepFaune.

    DeepFaune returns:
      - '' (empty string) when the prediction is not ready yet — return None.
      - 'undefined' when score < threshold — return as is, since it is a
        valid value for prediction_label (species_map.py maps it to None).
      - a real class 'roe deer', 'fox', ... — return in lower-case.
    """
    if value is None or value == '':
        return None
    return str(value).strip().lower()


def _safe_float(value) -> Optional[float]:
    """Float with guard against None/NaN."""
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
    """Int with guard against None/NaN."""
    if value is None:
        return None
    try:
        v = int(value)
        return v
    except (TypeError, ValueError):
        return None


def _bbox_to_dict(bbox) -> Optional[dict]:
    """Convert a numpy-array bbox to a plain dict for JSONB.

    DeepFaune returns the bbox as [x1, y1, x2, y2] in normalized coordinates
    (0..1 of the image dimensions).
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
