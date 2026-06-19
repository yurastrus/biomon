# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for DeepFauneAdapter — the real model on real photos.

RUNNING (from biomon-ai-venv, which has torch+ultralytics):
    /c/Users/IuriiStrus/repositories/biomon-ai-venv/Scripts/python \
        -m services.biomon_ai.test_deepfaune_integration

Or from the biomon root:
    DEEPFAUNE_PATH=C:/Users/IuriiStrus/repositories/deepfaune-src-1.4.1-08112025 \
    /c/Users/IuriiStrus/repositories/biomon-ai-venv/Scripts/python -m unittest \
        services.biomon_ai.test_deepfaune_integration -v

What they cover:
  • The adapter correctly imports DeepFaune and loads the models.
  • Predictions on reference photos from testdata/ match expectations.
  • DeepFaune reorders files by EXIF date → input_order ≠ output_order.
    The adapter must return the result in the INPUT order.
  • Confidence > threshold → prediction_label is valid; otherwise 'undefined'.
  • Special classes empty/human/vehicle work and human_count is reported.

Tests are SKIPped if DeepFaune is not available.
"""

import os
import unittest

# Path to DeepFaune. Defaults to a local dev copy.
DEEPFAUNE_PATH = os.environ.get(
    'DEEPFAUNE_PATH',
    'C:/Users/IuriiStrus/repositories/deepfaune-src-1.4.1-08112025'
)
TESTDATA_DIR = os.path.join(DEEPFAUNE_PATH, 'testdata')


def _testdata_available() -> bool:
    """Are DeepFaune and its testdata available?"""
    if not os.path.isdir(TESTDATA_DIR):
        return False
    if not os.path.exists(os.path.join(DEEPFAUNE_PATH, 'predictTools.py')):
        return False
    # Check that at least one set of weights is present
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
    f'DeepFaune not found at {DEEPFAUNE_PATH} or torch is not installed '
    '(biomon-ai-venv required).'
)
class TestDeepFauneAdapter(unittest.TestCase):
    """Runs with the real model. The model is loaded once per class
    (cls.adapter) — this is slow (~10-30 sec), so we spread requests across
    a single adapter for all tests."""

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

    # ── Canonical predictions ─────────────────────────────────────────

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

    # ── Special classes ───────────────────────────────────────────────

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

    # ── Series of several photos of the same species ──────────────────

    def test_sequence_of_same_species(self):
        """3 roe deer photos in one series → all get the same sequence-aware prediction."""
        paths = [
            self._path('roedeer11.JPG'),
            self._path('roedeer12.JPG'),
            self._path('roedeer13.JPG'),
        ]
        results = self.adapter.predict_observation(paths)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.prediction_label, 'roe deer')

    # ── Output ORDER check (input order != EXIF order) ────────────────

    def test_output_preserves_input_order(self):
        """DeepFaune internally sorts by EXIF date — the adapter must return
        the result in the input order, not by date."""
        # roedeer11 is dated 2021, wildboar11 — 2019: by EXIF DeepFaune would
        # return wildboar first. If the input imposes a different order, the
        # adapter must respect it.
        input_paths = [
            self._path('roedeer11.JPG'),    # 2021
            self._path('wildboar11.JPG'),   # 2019
            self._path('fox1.JPG'),         # no EXIF
        ]
        results = self.adapter.predict_observation(input_paths)
        # Check that each result's photo_path matches the input
        for input_p, result in zip(input_paths, results):
            self.assertEqual(
                result.photo_path, input_p,
                f'Output order mismatch: input={input_p}, got photo_path={result.photo_path}'
            )
        # And the predictions are correct
        self.assertEqual(results[0].prediction_label, 'roe deer')
        self.assertEqual(results[1].prediction_label, 'wild boar')
        self.assertEqual(results[2].prediction_label, 'fox')

    # ── Mixed observation (different species together) — must also map right ─

    def test_mixed_species(self):
        """Photos of different species in one batch — each gets its correct class."""
        input_paths = [
            self._path('roedeer11.JPG'),
            self._path('fox1.JPG'),
            self._path('badger.JPG'),
            self._path('empty1.JPG'),
        ]
        results = self.adapter.predict_observation(input_paths)
        labels = [r.prediction_label for r in results]
        # Order: roe deer, fox, badger, empty (in input order)
        self.assertEqual(labels, ['roe deer', 'fox', 'badger', 'empty'])

    # ── Empty/edge ────────────────────────────────────────────────────

    def test_empty_input(self):
        results = self.adapter.predict_observation([])
        self.assertEqual(results, [])

    # ── Integration with species_map ──────────────────────────────────

    def test_predictions_map_to_species_ids(self):
        """Check that raw labels from DeepFaune map to species_id."""
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
        print(f'SKIP: DeepFaune not found at {DEEPFAUNE_PATH}')
        print('Or torch is not installed in the active venv.')
        print('Must be run from biomon-ai-venv.')
    unittest.main(verbosity=2)
