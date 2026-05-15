"""Абстракція над AI-класифікатором.

Worker не знає, який саме класифікатор працює (DeepFaune, MegaDetector,
кастомна модель). Він дзвонить методом `predict_observation()` і отримує
список `PhotoPrediction`. Усі деталі моделі ховаються в адаптері.

ЯК ДОДАТИ НОВУ МОДЕЛЬ:
    1. Створити клас, що наслідує IClassifier.
    2. Реалізувати `name`, `version`, `config`, `predict_observation()`.
    3. Якщо нова модель видає інші labels — оновити `species_map.py`.
    4. У worker.py змінити імпорт або зробити вибір adapter'а через
       конфіг (`AI_RUNNER.MODEL_NAME`).

Існуючі адаптери:
    StubAdapter      — не використовує модель, повертає фіксовані прогнози.
                       Для unit-тестів і dev-машин без torch.
    DeepFauneAdapter — буде в Кроці 8. Обгортка над PredictorImage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────
# Структура одного прогнозу (про одне фото)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class PhotoPrediction:
    """Прогноз AI для одного фото. Worker мапить це на запис у ai_predictions.

    Усі поля Optional, бо:
      - Якщо модель не зробила висновок (score < threshold), prediction_label
        буде None, але top1_label усе одно заповнений.
      - Якщо обробка фото впала (зіпсований JPEG, OOM) — заповнюється
        лише `error`, інші поля None.
    """
    photo_path: str                              # абсолютний шлях, по якому worker знаходить photo_id

    # Sequence-aware прогноз (модель агрегує по серії)
    prediction_label: Optional[str] = None       # 'roe deer', 'empty', тощо. None якщо < threshold
    prediction_score: Optional[float] = None     # 0..1

    # Per-photo (без агрегації по серії)
    base_label:       Optional[str] = None
    base_score:       Optional[float] = None

    # Завжди заповнений top-1 клас, незалежно від threshold
    top1_label:       Optional[str] = None
    top1_score:       Optional[float] = None

    # Допоміжні поля від детектора
    animal_count:     Optional[int] = None
    human_count:      Optional[int] = None
    bbox:             Optional[dict] = None      # буде записаний у JSONB

    # Якщо обробка цього фото впала — інші поля None, error заповнено
    error:            Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Абстрактний інтерфейс класифікатора
# ─────────────────────────────────────────────────────────────────────

class IClassifier(ABC):
    """Базовий клас для адаптерів AI-моделей."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Назва моделі, напр. 'DeepFaune'. Йде в `ai_models.name`."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Версія моделі, напр. '1.4.1'. Йде в `ai_models.version`."""

    @property
    def config(self) -> dict:
        """Метадані конфігурації (threshold, detector тощо).
        Йде в `ai_models.config_json`. Можна override.
        """
        return {}

    @abstractmethod
    def predict_observation(self, photo_paths: List[str]) -> List[PhotoPrediction]:
        """Прогноз для однієї observation (= однієї послідовності фото).

        Args:
            photo_paths: список абсолютних шляхів до фото В ХРОНОЛОГІЧНОМУ
                         ПОРЯДКУ. Worker передає всі фото однієї observation.

        Returns:
            Список PhotoPrediction довжиною len(photo_paths), у тому ж порядку.
            Кожен елемент — або заповнений прогноз, або PhotoPrediction(error=...)
            якщо саме це фото не вдалося обробити.

        Raises:
            Якщо вся observation не вдалася (модель крашнула, OOM) — підіймає
            виняток. Worker зловить і запише observation як failed.
        """


# ─────────────────────────────────────────────────────────────────────
# StubAdapter: фейкові прогнози для тестів без torch
# ─────────────────────────────────────────────────────────────────────

class StubAdapter(IClassifier):
    """Адаптер-заглушка. Повертає однаковий прогноз для всіх фото в observation.

    Призначення:
        • Unit-тести worker'а на dev-машині без torch/DeepFaune.
        • Smoke-тест на сервері перед інсталяцією моделі.
        • Перевірка що БД-запис коректний.

    Приклад:
        adapter = StubAdapter(label='roe deer', score=0.92)
        predictions = adapter.predict_observation(['/path/a.jpg', '/path/b.jpg'])
        # обидва PhotoPrediction матимуть prediction_label='roe deer', score=0.92
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
            'note': 'Тестовий заглушковий адаптер. Не для продакшну.',
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
