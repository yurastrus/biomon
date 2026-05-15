"""Інтеграційні тести DeepFauneAdapter — справжня модель на справжніх фото.

ЗАПУСК (з biomon-ai-venv, який має torch+ultralytics):
    /c/Users/IuriiStrus/repositories/biomon-ai-venv/Scripts/python \
        -m services.biomon_ai.test_deepfaune_integration

Або з кореня біомону:
    DEEPFAUNE_PATH=C:/Users/IuriiStrus/repositories/deepfaune-src-1.4.1-08112025 \
    /c/Users/IuriiStrus/repositories/biomon-ai-venv/Scripts/python -m unittest \
        services.biomon_ai.test_deepfaune_integration -v

Що покривають:
  • Адаптер коректно імпортує DeepFaune і завантажує моделі.
  • Прогнози на еталонних фото з testdata/ збігаються з очікуваннями.
  • DeepFaune перевпорядковує файли за EXIF date → input_order ≠ output_order.
    Адаптер має повернути результат у ВХІДНОМУ порядку.
  • Confidence > threshold → prediction_label валідне; інакше 'undefined'.
  • Спецкласи empty/human/vehicle працюють і human_count відображається.

Тести SKIP-ються якщо немає DeepFaune.
"""

import os
import unittest

# Шлях до DeepFaune. За замовчуванням — локальна dev-копія.
DEEPFAUNE_PATH = os.environ.get(
    'DEEPFAUNE_PATH',
    'C:/Users/IuriiStrus/repositories/deepfaune-src-1.4.1-08112025'
)
TESTDATA_DIR = os.path.join(DEEPFAUNE_PATH, 'testdata')


def _testdata_available() -> bool:
    """Чи доступні DeepFaune і його testdata?"""
    if not os.path.isdir(TESTDATA_DIR):
        return False
    if not os.path.exists(os.path.join(DEEPFAUNE_PATH, 'predictTools.py')):
        return False
    # Перевіряємо наявність хоча б одних ваг
    for w in [
        'deepfaune-yolov8s_960.pt',
        'deepfaune-vit_large_patch14_dinov2.lvd142m.v4.pt',
    ]:
        if not os.path.exists(os.path.join(DEEPFAUNE_PATH, w)):
            return False
    try:
        import torch  # noqa
    except ImportError:
        return False
    return True


_AVAILABLE = _testdata_available()


@unittest.skipUnless(
    _AVAILABLE,
    f'DeepFaune не знайдено в {DEEPFAUNE_PATH} або torch не встановлено '
    '(потрібен biomon-ai-venv).'
)
class TestDeepFauneAdapter(unittest.TestCase):
    """Прогони з реальною моделлю. Модель завантажується один раз на клас
    (cls.adapter) — це довго (~10-30 сек), тому розкидаємо запити в один
    адаптер для всіх тестів."""

    @classmethod
    def setUpClass(cls):
        from services.biomon_ai.deepfaune_adapter import DeepFauneAdapter
        cls.adapter = DeepFauneAdapter(
            deepfaune_path=DEEPFAUNE_PATH,
            threshold=0.8,
        )

    def _path(self, filename: str) -> str:
        return os.path.join(TESTDATA_DIR, filename)

    # ── Metadata ──────────────────────────────────────────────────────

    def test_metadata(self):
        self.assertEqual(self.adapter.name, 'DeepFaune')
        self.assertEqual(self.adapter.version, '1.4.1')
        cfg = self.adapter.config
        self.assertEqual(cfg['threshold'], 0.8)
        self.assertFalse(cfg['birdclassification'])
        self.assertIn('classifier', cfg)

    # ── Каноничні прогнози ────────────────────────────────────────────

    def test_roe_deer(self):
        results = self.adapter.predict_observation([self._path('roedeer11.JPG')])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.prediction_label, 'roe deer')
        self.assertEqual(r.top1_label, 'roe deer')
        self.assertGreater(r.prediction_score, 0.8)
        self.assertEqual(r.animal_count, 1)
        self.assertEqual(r.human_count, 0)

    def test_fox(self):
        results = self.adapter.predict_observation([self._path('fox1.JPG')])
        self.assertEqual(results[0].prediction_label, 'fox')

    def test_wild_boar(self):
        results = self.adapter.predict_observation([self._path('wildboar11.JPG')])
        self.assertEqual(results[0].prediction_label, 'wild boar')

    def test_badger(self):
        results = self.adapter.predict_observation([self._path('badger.JPG')])
        self.assertEqual(results[0].prediction_label, 'badger')

    def test_wolf(self):
        results = self.adapter.predict_observation([self._path('wolf1.JPG')])
        self.assertEqual(results[0].prediction_label, 'wolf')

    # ── Спецкласи ─────────────────────────────────────────────────────

    def test_empty(self):
        results = self.adapter.predict_observation([self._path('empty1.JPG')])
        r = results[0]
        self.assertEqual(r.prediction_label, 'empty')
        self.assertEqual(r.animal_count, 0)
        self.assertEqual(r.human_count, 0)

    def test_human(self):
        results = self.adapter.predict_observation([self._path('human11.JPG')])
        r = results[0]
        self.assertEqual(r.prediction_label, 'human')
        self.assertGreaterEqual(r.human_count, 1)

    def test_vehicle(self):
        results = self.adapter.predict_observation([self._path('vehicle.JPG')])
        self.assertEqual(results[0].prediction_label, 'vehicle')

    # ── Серія з декількох фото одного виду ────────────────────────────

    def test_sequence_of_same_species(self):
        """3 фото козулі в одній серії → всі мають однаковий sequence-aware prediction."""
        paths = [
            self._path('roedeer11.JPG'),
            self._path('roedeer12.JPG'),
            self._path('roedeer13.JPG'),
        ]
        results = self.adapter.predict_observation(paths)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.prediction_label, 'roe deer')

    # ── Перевірка PORYADKU виходу (input order != EXIF order) ─────────

    def test_output_preserves_input_order(self):
        """DeepFaune внутрішньо сортує за EXIF date — адаптер має повернути
        результат у вхідному порядку, не у датах."""
        # roedeer11 датована 2021, wildboar11 — 2019: за EXIF DeepFaune
        # поверне wildboar першим. Якщо input нав'язує інший порядок,
        # адаптер повинен його дотримуватись.
        input_paths = [
            self._path('roedeer11.JPG'),    # 2021
            self._path('wildboar11.JPG'),   # 2019
            self._path('fox1.JPG'),         # без EXIF
        ]
        results = self.adapter.predict_observation(input_paths)
        # Перевіряємо що photo_path кожного результату відповідає input
        for input_p, result in zip(input_paths, results):
            self.assertEqual(
                result.photo_path, input_p,
                f'Output order mismatch: input={input_p}, got photo_path={result.photo_path}'
            )
        # І прогнози правильні
        self.assertEqual(results[0].prediction_label, 'roe deer')
        self.assertEqual(results[1].prediction_label, 'wild boar')
        self.assertEqual(results[2].prediction_label, 'fox')

    # ── Mixed observation (різні види разом) — теж має правильно мапати ─

    def test_mixed_species(self):
        """Фото різних видів в одному батчі — кожне має свій правильний клас."""
        input_paths = [
            self._path('roedeer11.JPG'),
            self._path('fox1.JPG'),
            self._path('badger.JPG'),
            self._path('empty1.JPG'),
        ]
        results = self.adapter.predict_observation(input_paths)
        labels = [r.prediction_label for r in results]
        # Порядок: roe deer, fox, badger, empty (у вхідному порядку)
        self.assertEqual(labels, ['roe deer', 'fox', 'badger', 'empty'])

    # ── Порожній/edge ─────────────────────────────────────────────────

    def test_empty_input(self):
        results = self.adapter.predict_observation([])
        self.assertEqual(results, [])

    # ── Інтеграція з species_map ──────────────────────────────────────

    def test_predictions_map_to_species_ids(self):
        """Перевіряємо що сирі labels від DeepFaune мапаються у species_id."""
        from services.biomon_ai.species_map import map_deepfaune_label

        results = self.adapter.predict_observation([
            self._path('roedeer11.JPG'),
            self._path('fox1.JPG'),
            self._path('empty1.JPG'),
        ])

        species_ids = [map_deepfaune_label(r.prediction_label) for r in results]
        self.assertEqual(species_ids[0], 4)    # Capreolus capreolus
        self.assertEqual(species_ids[1], 10)   # Vulpes vulpes
        self.assertEqual(species_ids[2], -1)   # empty


if __name__ == '__main__':
    if not _AVAILABLE:
        print(f'SKIP: DeepFaune не знайдено в {DEEPFAUNE_PATH}')
        print('Або torch не встановлено в активному venv.')
        print('Має запускатись з biomon-ai-venv.')
    unittest.main(verbosity=2)
