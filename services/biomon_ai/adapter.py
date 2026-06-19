# SPDX-License-Identifier: AGPL-3.0-only
"""Abstraction over the AI classifier.

The worker does not know which classifier is running (DeepFaune, MegaDetector,
a custom model). It calls `predict_observation()` and gets back a list of
`PhotoPrediction`. All model details are hidden inside the adapter.

HOW TO ADD A NEW MODEL:
    1. Create a class that inherits from IClassifier.
    2. Implement `name`, `version`, `config`, `predict_observation()`.
    3. If the new model emits different labels — update `species_map.py`.
    4. In worker.py change the import or select the adapter via
       config (`AI_RUNNER.MODEL_NAME`).

Existing adapters:
    StubAdapter      — does not use a model, returns fixed predictions.
                       For unit tests and dev machines without torch.
    DeepFauneAdapter — comes in Step 8. A wrapper over PredictorImage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────
# Structure of a single prediction (for one photo)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class PhotoPrediction:
    """AI prediction for a single photo. The worker maps it onto an ai_predictions row.

    All fields are Optional, because:
      - If the model did not reach a conclusion (score < threshold), prediction_label
        will be None, but top1_label is still filled in.
      - If processing the photo failed (corrupt JPEG, OOM) — only `error`
        is filled in, the other fields are None.
    """
    photo_path: str                              # absolute path the worker uses to find photo_id

    # Sequence-aware prediction (model aggregates over the series)
    prediction_label: Optional[str] = None       # 'roe deer', 'empty', etc. None if < threshold
    prediction_score: Optional[float] = None     # 0..1

    # Per-photo (no aggregation over the series)
    base_label:       Optional[str] = None
    base_score:       Optional[float] = None

    # Always-filled top-1 class, regardless of threshold
    top1_label:       Optional[str] = None
    top1_score:       Optional[float] = None

    # Auxiliary fields from the detector
    animal_count:     Optional[int] = None
    human_count:      Optional[int] = None
    bbox:             Optional[dict] = None      # will be written to JSONB

    # If processing this photo failed — other fields None, error filled in
    error:            Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Abstract classifier interface
# ─────────────────────────────────────────────────────────────────────

class IClassifier(ABC):
    """Base class for AI model adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Model name, e.g. 'DeepFaune'. Goes into `ai_models.name`."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Model version, e.g. '1.4.1'. Goes into `ai_models.version`."""

    @property
    def config(self) -> dict:
        """Configuration metadata (threshold, detector, etc.).
        Goes into `ai_models.config_json`. Can be overridden.
        """
        return {}

    @abstractmethod
    def predict_observation(self, photo_paths: List[str]) -> List[PhotoPrediction]:
        """Prediction for a single observation (= a single photo sequence).

        Args:
            photo_paths: list of absolute photo paths IN CHRONOLOGICAL
                         ORDER. The worker passes all photos of one observation.

        Returns:
            A list of PhotoPrediction of length len(photo_paths), in the same order.
            Each element is either a filled-in prediction, or PhotoPrediction(error=...)
            if that particular photo could not be processed.

        Raises:
            If the whole observation failed (model crashed, OOM) — raises an
            exception. The worker catches it and records the observation as failed.
        """


# ─────────────────────────────────────────────────────────────────────
# StubAdapter: fake predictions for tests without torch
# ─────────────────────────────────────────────────────────────────────

class StubAdapter(IClassifier):
    """Stub adapter. Returns the same prediction for all photos in an observation.

    Purpose:
        • Unit tests of the worker on a dev machine without torch/DeepFaune.
        • Smoke test on the server before installing the model.
        • Checking that the DB record is correct.

    Example:
        adapter = StubAdapter(label='roe deer', score=0.92)
        predictions = adapter.predict_observation(['/path/a.jpg', '/path/b.jpg'])
        # both PhotoPrediction will have prediction_label='roe deer', score=0.92
    """

    def __init__(
        self,
        label: str = 'roe deer',
        score: float = 0.95,
        animal_count: int = 1,
        human_count: int = 0,
        bbox: Optional[dict] = None,
    ):
        self._label = label
        self._score = score
        self._animal_count = animal_count
        self._human_count = human_count
        self._bbox = bbox or {'x1': 100, 'y1': 100, 'x2': 500, 'y2': 400}

    @property
    def name(self) -> str:
        return 'Stub'

    @property
    def version(self) -> str:
        return '0.0.1'

    @property
    def config(self) -> dict:
        return {
            'fixed_label': self._label,
            'fixed_score': self._score,
            'note': 'Test stub adapter. Not for production.',
        }

    def predict_observation(self, photo_paths: List[str]) -> List[PhotoPrediction]:
        return [
            PhotoPrediction(
                photo_path=p,
                prediction_label=self._label,
                prediction_score=self._score,
                base_label=self._label,
                base_score=self._score,
                top1_label=self._label,
                top1_score=self._score,
                animal_count=self._animal_count,
                human_count=self._human_count,
                bbox=self._bbox,
            )
            for p in photo_paths
        ]
