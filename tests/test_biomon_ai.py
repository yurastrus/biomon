"""Tests for services/biomon_ai/.

Covers:
  • species_map.py    — mapping DeepFaune labels → Species.id
  • adapter.py        — IClassifier ABC, StubAdapter, PhotoPrediction
  • worker.py         — orchestration with mocked DB and adapter
  • cli.py            — CLI parsing, error paths

DeepFauneAdapter integration tests live in a separate file, since they require
torch+ultralytics installed in biomon-ai-venv. Run separately:
    /c/Users/IuriiStrus/repositories/biomon-ai-venv/Scripts/python \
        -m services.biomon_ai.test_deepfaune_integration
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch

from services.biomon_ai.adapter import (
    IClassifier,
    PhotoPrediction,
    StubAdapter,
)
from services.biomon_ai.species_map import (
    DEEPFAUNE_TO_SPECIES_ID,
    map_deepfaune_label,
)


# ════════════════════════════════════════════════════════════════════════════
# 1. SPECIES MAPPING
# ════════════════════════════════════════════════════════════════════════════

class TestSpeciesMap(unittest.TestCase):
    """Mapping DeepFaune labels → biomon Species.id."""

    # ── Special classes (-1, -3, -5) ───────────────────────────────
    def test_empty_maps_to_minus_1(self):
        self.assertEqual(map_deepfaune_label('empty'), -1)

    def test_human_maps_to_minus_5(self):
        self.assertEqual(map_deepfaune_label('human'), -5)

    def test_vehicle_maps_to_minus_3(self):
        self.assertEqual(map_deepfaune_label('vehicle'), -3)

    # ── Main Carpathian mammals ─────────────────────────────────────
    def test_carpathian_mammals_mapped(self):
        """All species that actually occur in the Carpathians and exist in Species."""
        cases = [
            ('roe deer', 4),     ('red deer', 5),  ('wild boar', 3),
            ('fox', 10),         ('wolf', 9),      ('lynx', 37),
            ('bear', 36),        ('badger', 11),   ('moose', 6),
            ('bison', 38),       ('squirrel', 2),  ('raccoon dog', 26),
            ('dog', 8),          ('cat', 7),
        ]
        for label, expected_id in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    map_deepfaune_label(label), expected_id,
                    f'{label!r} should map to species_id={expected_id}'
                )

    def test_lagomorph_maps_to_hare(self):
        """Species has only one lagomorph (brown hare, id=1)."""
        self.assertEqual(map_deepfaune_label('lagomorph'), 1)

    def test_micromammal_maps_to_generic_rodent(self):
        """DeepFaune micromammal → 'Дрібний гризун' (small rodent, -8)."""
        self.assertEqual(map_deepfaune_label('micromammal'), -8)

    # ── Species not present in Carpathians/Species → None ───────────
    def test_non_carpathian_species_unmapped(self):
        for label in [
            'ibex', 'beaver', 'wolverine', 'genet', 'mustelid', 'otter',
            'hedgehog', 'porcupine', 'nutria', 'raccoon', 'reindeer',
            'fallow deer', 'chamois', 'marmot', 'mouflon',
            'golden jackal', 'goat', 'equid', 'muskrat',
        ]:
            with self.subTest(label=label):
                self.assertIsNone(
                    map_deepfaune_label(label),
                    f'{label!r} should be None (not in biomon Species)'
                )

    # ── Domestic animals now mapped to special Species ──────────────
    def test_cow_and_sheep_map_to_special_species(self):
        self.assertEqual(map_deepfaune_label('cow'), -10)
        self.assertEqual(map_deepfaune_label('sheep'), -11)

    # ── Birds: 8 DeepFaune sub-classes + fallback ───────────────────
    def test_all_bird_subclasses_mapped(self):
        """birdclassification=True → labels in 'bird <subclass>' format (space)."""
        cases = [
            ('bird anseriform',  -12),
            ('bird columbiform', -13),
            ('bird corvid',      -14),
            ('bird galliform',   -15),
            ('bird piciform',    -16),
            ('bird raptor',      -17),
            ('bird otherbird',   -18),
            ('bird passerine',   -9),
            ('bird undefined',   -18),  # sub-classifier < threshold
            ('bird',             -18),  # legacy / birdclassification=False
        ]
        for label, expected_id in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    map_deepfaune_label(label), expected_id,
                    f'{label!r} should map to species_id={expected_id}'
                )

    # ── Special 'undefined' value from DeepFaune (score<threshold) ──
    def test_undefined_returns_none(self):
        self.assertIsNone(map_deepfaune_label('undefined'))

    # ── Edge-cases ─────────────────────────────────────────────────
    def test_case_insensitive(self):
        self.assertEqual(map_deepfaune_label('ROE DEER'), 4)
        self.assertEqual(map_deepfaune_label('Roe Deer'), 4)
        self.assertEqual(map_deepfaune_label('roe DEER'), 4)

    def test_whitespace_stripped(self):
        self.assertEqual(map_deepfaune_label('  roe deer  '), 4)
        self.assertEqual(map_deepfaune_label('\troe deer\n'), 4)

    def test_none_input(self):
        self.assertIsNone(map_deepfaune_label(None))

    def test_empty_string(self):
        self.assertIsNone(map_deepfaune_label(''))

    def test_unknown_label(self):
        """Guard against new DeepFaune versions with new classes."""
        self.assertIsNone(map_deepfaune_label('alien-species-not-in-map'))

    # ── Structural checks on the map ────────────────────────────────
    def test_no_duplicate_keys(self):
        # A duplicated key in the map would be a bug.
        keys_lower = [k.lower() for k in DEEPFAUNE_TO_SPECIES_ID.keys()]
        self.assertEqual(len(keys_lower), len(set(keys_lower)))

    def test_all_mapped_ids_are_integers(self):
        for label, sp_id in DEEPFAUNE_TO_SPECIES_ID.items():
            if sp_id is not None:
                self.assertIsInstance(sp_id, int, f'{label!r} maps to {sp_id!r}')

    def test_no_double_mapping_for_positive_species(self):
        # No positive species_id should appear in the map twice.
        # Exceptions are the special classes -1, -3, -5, -8 (in theory one
        # Species could represent several DeepFaune labels, but for positive
        # ones that would mean an error — real species are unique).
        positive_ids = [v for v in DEEPFAUNE_TO_SPECIES_ID.values() if v is not None and v > 0]
        self.assertEqual(len(positive_ids), len(set(positive_ids)))


# ════════════════════════════════════════════════════════════════════════════
# 2. ADAPTER (IClassifier + StubAdapter + PhotoPrediction)
# ════════════════════════════════════════════════════════════════════════════

class TestIClassifier(unittest.TestCase):
    """The ABC must not be directly instantiable."""

    def test_iclassifier_is_abstract(self):
        with self.assertRaises(TypeError):
            IClassifier()

    def test_subclass_must_implement_methods(self):
        class IncompleteAdapter(IClassifier):
            pass  # no implementations
        with self.assertRaises(TypeError):
            IncompleteAdapter()


class TestPhotoPrediction(unittest.TestCase):
    """DTO for a single photo prediction."""

    def test_minimal_construction(self):
        p = PhotoPrediction(photo_path='/a.jpg')
        self.assertEqual(p.photo_path, '/a.jpg')
        self.assertIsNone(p.prediction_label)
        self.assertIsNone(p.prediction_score)
        self.assertIsNone(p.bbox)
        self.assertIsNone(p.error)

    def test_full_construction(self):
        p = PhotoPrediction(
            photo_path='/a.jpg',
            prediction_label='roe deer',
            prediction_score=0.95,
            base_label='roe deer',
            base_score=0.95,
            top1_label='roe deer',
            top1_score=0.95,
            animal_count=1,
            human_count=0,
            bbox={'x1': 0.1, 'y1': 0.2, 'x2': 0.5, 'y2': 0.6},
        )
        self.assertEqual(p.prediction_label, 'roe deer')
        self.assertEqual(p.bbox['x1'], 0.1)

    def test_error_field(self):
        p = PhotoPrediction(photo_path='/a.jpg', error='broken jpeg')
        self.assertEqual(p.error, 'broken jpeg')
        self.assertIsNone(p.prediction_label)


class TestStubAdapter(unittest.TestCase):
    """StubAdapter — deterministic DeepFaune replacement for unit tests."""

    def test_metadata(self):
        adapter = StubAdapter()
        self.assertEqual(adapter.name, 'Stub')
        self.assertEqual(adapter.version, '0.0.1')
        self.assertIn('fixed_label', adapter.config)
        self.assertIn('note', adapter.config)

    def test_default_prediction(self):
        adapter = StubAdapter()
        results = adapter.predict_observation(['/x/a.jpg', '/x/b.jpg'])
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r.prediction_label, 'roe deer')
            self.assertEqual(r.prediction_score, 0.95)
            self.assertEqual(r.base_label, 'roe deer')
            self.assertEqual(r.top1_label, 'roe deer')
            self.assertEqual(r.animal_count, 1)
            self.assertEqual(r.human_count, 0)
            self.assertIsNotNone(r.bbox)

    def test_custom_label(self):
        adapter = StubAdapter(label='empty', score=0.99, animal_count=0)
        result = adapter.predict_observation(['/x/a.jpg'])[0]
        self.assertEqual(result.prediction_label, 'empty')
        self.assertEqual(result.animal_count, 0)

    def test_preserves_input_order(self):
        adapter = StubAdapter()
        paths = ['/x/zzz.jpg', '/x/aaa.jpg', '/x/mmm.jpg']
        results = adapter.predict_observation(paths)
        for path, result in zip(paths, results):
            self.assertEqual(result.photo_path, path)

    def test_empty_input(self):
        adapter = StubAdapter()
        self.assertEqual(adapter.predict_observation([]), [])

    def test_returns_independent_objects(self):
        """Stub must not return the same instance for all photos."""
        adapter = StubAdapter()
        results = adapter.predict_observation(['/x/a.jpg', '/x/b.jpg'])
        self.assertIsNot(results[0], results[1])


# ════════════════════════════════════════════════════════════════════════════
# 3. WORKER (process_batch + run_from_queue with mocks)
# ════════════════════════════════════════════════════════════════════════════

class TestProcessBatch(unittest.TestCase):
    """Orchestrator tests without a real DB or files."""

    def setUp(self):
        # Patch all DB functions at once
        self.engine_patch = patch('services.biomon_ai.worker.make_engine')
        self.session_patch = patch('services.biomon_ai.worker.make_session')
        self.get_model_patch = patch('services.biomon_ai.worker.get_or_create_model')
        self.pick_patch = patch('services.biomon_ai.worker.pick_pending_observations')
        self.save_patch = patch('services.biomon_ai.worker.save_observation_predictions')
        # The per-series pause gate is checked inside the loop. Default it to
        # "not paused" so the existing tests run end-to-end; the pause-specific
        # tests override .side_effect / .return_value.
        self.paused_patch = patch('services.biomon_ai.worker.is_ai_paused_on_session')

        self.mock_engine = self.engine_patch.start()
        self.mock_session_factory = self.session_patch.start()
        self.mock_get_model = self.get_model_patch.start()
        self.mock_pick = self.pick_patch.start()
        self.mock_save = self.save_patch.start()
        self.mock_paused = self.paused_patch.start()
        self.mock_paused.return_value = False

        self.mock_session = MagicMock()
        self.mock_session_factory.return_value = self.mock_session
        self.mock_get_model.return_value = 42

    def tearDown(self):
        for p in [
            self.engine_patch, self.session_patch, self.get_model_patch,
            self.pick_patch, self.save_patch, self.paused_patch,
        ]:
            p.stop()

    def test_empty_pending_returns_zero(self):
        from services.biomon_ai.worker import process_batch
        self.mock_pick.return_value = []
        result = process_batch(
            adapter=StubAdapter(),
            upload_path='/tmp/fake',
            max_observations=10,
        )
        self.assertEqual(result, 0)
        self.mock_save.assert_not_called()

    def test_skips_observations_with_no_files_on_disk(self):
        from services.biomon_ai.db import PendingObservation
        from services.biomon_ai.worker import process_batch

        self.mock_pick.return_value = [
            PendingObservation(
                observation_id=1,
                photos=[(100, 'no_such_file.jpg')],
            ),
        ]
        result = process_batch(
            adapter=StubAdapter(),
            upload_path='/nonexistent',
            max_observations=10,
        )
        self.assertEqual(result, 0)
        self.mock_save.assert_not_called()

    def test_processes_observation_with_real_files(self):
        """If files exist on disk — the adapter runs and save is called."""
        import tempfile
        from services.biomon_ai.db import PendingObservation
        from services.biomon_ai.worker import process_batch

        # Create temporary "photos" with the expected layout
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = os.path.join(tmpdir, 'pending_photos', 'raw')
            os.makedirs(raw_dir)
            fake_file = os.path.join(raw_dir, 'fake.jpg')
            with open(fake_file, 'wb') as f:
                f.write(b'\xff\xd8\xff\xe0fake')  # JPEG magic, not valid

            self.mock_pick.return_value = [
                PendingObservation(
                    observation_id=1,
                    photos=[(100, 'fake.jpg')],
                ),
            ]
            self.mock_save.return_value = 1

            result = process_batch(
                adapter=StubAdapter(),
                upload_path=tmpdir,
                max_observations=10,
            )
            self.assertEqual(result, 1)
            self.mock_save.assert_called_once()
            # Check that predictions from the adapter were passed through
            kwargs = self.mock_save.call_args.kwargs
            self.assertEqual(kwargs['observation_id'], 1)
            self.assertEqual(kwargs['model_id'], 42)
            self.assertEqual(len(kwargs['predictions']), 1)

    def test_thumbnail_fallback_when_raw_missing(self):
        """If the raw file is missing but a thumbnail exists — worker uses it."""
        import tempfile
        from services.biomon_ai.db import PendingObservation
        from services.biomon_ai.worker import process_batch

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create ONLY the thumbnail (no raw)
            thumb_dir = os.path.join(tmpdir, 'pending_photos', 'thumbnails')
            os.makedirs(thumb_dir)
            with open(os.path.join(thumb_dir, 'only_thumb.jpg'), 'wb') as f:
                f.write(b'\xff\xd8\xff\xe0' + b'thumbnail-only')
            # raw/ is NOT even created

            self.mock_pick.return_value = [
                PendingObservation(observation_id=99, photos=[(500, 'only_thumb.jpg')]),
            ]
            self.mock_save.return_value = 1

            result = process_batch(
                adapter=StubAdapter(),
                upload_path=tmpdir,
                max_observations=10,
            )
            self.assertEqual(result, 1)
            self.mock_save.assert_called_once()
            # Check that the thumbnail path specifically was passed in predictions
            kwargs = self.mock_save.call_args.kwargs
            paths = list(kwargs['photo_id_by_path'].keys())
            self.assertEqual(len(paths), 1)
            self.assertIn('thumbnails', paths[0])

    def test_adapter_exception_does_not_stop_other_observations(self):
        """One crashed — the others keep going."""
        import tempfile
        from services.biomon_ai.db import PendingObservation
        from services.biomon_ai.worker import process_batch

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = os.path.join(tmpdir, 'pending_photos', 'raw')
            os.makedirs(raw_dir)
            for fn in ['a.jpg', 'b.jpg']:
                with open(os.path.join(raw_dir, fn), 'wb') as f:
                    f.write(b'\xff\xd8\xff\xe0')

            self.mock_pick.return_value = [
                PendingObservation(observation_id=1, photos=[(100, 'a.jpg')]),
                PendingObservation(observation_id=2, photos=[(200, 'b.jpg')]),
            ]

            # Adapter raises on the first observation, succeeds on the second
            class FlakyAdapter(StubAdapter):
                def __init__(inner_self):
                    super().__init__()
                    inner_self._call = 0
                def predict_observation(inner_self, paths):
                    inner_self._call += 1
                    if inner_self._call == 1:
                        raise RuntimeError('Simulated crash')
                    return super().predict_observation(paths)

            self.mock_save.return_value = 1
            result = process_batch(
                adapter=FlakyAdapter(),
                upload_path=tmpdir,
                max_observations=10,
            )
            # One crashed, the other went through
            self.assertEqual(result, 1)
            self.assertEqual(self.mock_save.call_count, 1)

    # ── Per-series pause gate (upload-in-progress) ──────────────────

    def test_pause_mid_batch_stops_after_current_series(self):
        """Pause flag flips ON after the 2nd series: the worker finishes the
        series already in flight and then stops at the TOP of the next loop
        iteration — it must NOT drain the whole batch."""
        import tempfile
        from services.biomon_ai.db import PendingObservation
        from services.biomon_ai.worker import process_batch

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = os.path.join(tmpdir, 'pending_photos', 'raw')
            os.makedirs(raw_dir)
            for fn in ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg', 'e.jpg']:
                with open(os.path.join(raw_dir, fn), 'wb') as f:
                    f.write(b'\xff\xd8\xff\xe0')

            # A 5-series "batch" — the upload starts during processing.
            self.mock_pick.return_value = [
                PendingObservation(observation_id=i, photos=[(100 + i, fn)])
                for i, fn in enumerate(['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg', 'e.jpg'])
            ]
            self.mock_save.return_value = 1

            # Pause gate: not paused before series #0 and #1, then paused —
            # checked at the TOP of the loop, so series #0 and #1 complete and
            # the check before series #2 trips the break.
            self.mock_paused.side_effect = [False, False, True]

            result = process_batch(
                adapter=StubAdapter(),
                upload_path=tmpdir,
                max_observations=5,
            )

            # Only the first two series were processed; the batch did NOT finish.
            self.assertEqual(result, 2)
            self.assertEqual(self.mock_save.call_count, 2)
            self.assertLess(result, 5, "must stop before draining the whole batch")
            # The gate was consulted per-series (3 checks: pass, pass, trip).
            self.assertEqual(self.mock_paused.call_count, 3)

    def test_pause_active_from_start_processes_nothing(self):
        """If the lease is already active when the batch begins, the very first
        series is skipped — zero processed."""
        import tempfile
        from services.biomon_ai.db import PendingObservation
        from services.biomon_ai.worker import process_batch

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = os.path.join(tmpdir, 'pending_photos', 'raw')
            os.makedirs(raw_dir)
            with open(os.path.join(raw_dir, 'a.jpg'), 'wb') as f:
                f.write(b'\xff\xd8\xff\xe0')

            self.mock_pick.return_value = [
                PendingObservation(observation_id=1, photos=[(100, 'a.jpg')]),
            ]
            self.mock_paused.return_value = True

            result = process_batch(
                adapter=StubAdapter(),
                upload_path=tmpdir,
                max_observations=10,
            )
            self.assertEqual(result, 0)
            self.mock_save.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# 4. PROCESS_BATCH_TRACKED (idle cron → delete record)
# ════════════════════════════════════════════════════════════════════════════

class TestProcessBatchTracked(unittest.TestCase):
    """process_batch_tracked: an idle cron run deletes the DB record."""

    def _run_tracked(self, process_batch_result: int) -> tuple:
        """Runs process_batch_tracked with the given process_batch result.
        Returns (result, sql_stmts) where sql_stmts is the list of executed SQL strings."""
        import services.biomon_ai.worker as mod

        mock_engine = MagicMock()
        mock_session = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        created_row = MagicMock()
        created_row.id = 42

        with patch.object(mod, 'make_engine', return_value=mock_engine), \
             patch.object(mod, 'make_session', return_value=mock_session), \
             patch.object(mod, 'AIRunQueue', return_value=created_row), \
             patch.object(mod, 'process_batch', return_value=process_batch_result):
            result = mod.process_batch_tracked(
                adapter=StubAdapter(),
                upload_path='/fake',
                max_observations=200,
            )

        sql_stmts = [str(c.args[0]) for c in mock_conn.execute.call_args_list if c.args]
        return result, sql_stmts

    def test_idle_cron_deletes_queue_record(self):
        """When processed=0 — the record is deleted, not updated."""
        result, sqls = self._run_tracked(process_batch_result=0)
        self.assertEqual(result, 0)
        self.assertTrue(any('DELETE' in s for s in sqls), f"Expected DELETE, got: {sqls}")
        self.assertFalse(any('UPDATE' in s for s in sqls), f"Expected no UPDATE, got: {sqls}")

    def test_active_cron_updates_record_done(self):
        """When processed>0 — the record is updated to status='done'."""
        result, sqls = self._run_tracked(process_batch_result=5)
        self.assertEqual(result, 5)
        self.assertTrue(any('UPDATE' in s for s in sqls), f"Expected UPDATE, got: {sqls}")
        self.assertFalse(any('DELETE' in s for s in sqls), f"Expected no DELETE, got: {sqls}")


# ════════════════════════════════════════════════════════════════════════════
# 5. CLI parsing
# ════════════════════════════════════════════════════════════════════════════

class TestCLIParsing(unittest.TestCase):
    """argparse tests — we do not run main()."""

    def test_help_exits_zero(self):
        from services.biomon_ai.cli import parse_args
        with self.assertRaises(SystemExit) as cm:
            with redirect_stderr(io.StringIO()):
                # argparse pipes --help to stdout, sys.exit(0)
                with patch('sys.stdout', io.StringIO()):
                    parse_args(['--help'])
        self.assertEqual(cm.exception.code, 0)

    def test_requires_mode(self):
        from services.biomon_ai.cli import parse_args
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                parse_args([])

    def test_batch_and_from_queue_mutex(self):
        from services.biomon_ai.cli import parse_args
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                parse_args(['--batch=5', '--from-queue'])

    def test_batch_parses(self):
        from services.biomon_ai.cli import parse_args
        args = parse_args(['--batch=50'])
        self.assertEqual(args.batch, 50)
        self.assertFalse(args.from_queue)
        self.assertEqual(args.adapter, 'deepfaune')  # default

    def test_from_queue_parses(self):
        from services.biomon_ai.cli import parse_args
        args = parse_args(['--from-queue', '--adapter=stub'])
        self.assertTrue(args.from_queue)
        self.assertEqual(args.adapter, 'stub')

    def test_threshold_override(self):
        from services.biomon_ai.cli import parse_args
        args = parse_args(['--batch=10', '--threshold=0.5'])
        self.assertEqual(args.threshold, 0.5)

    def test_invalid_adapter_choice(self):
        from services.biomon_ai.cli import parse_args
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                parse_args(['--batch=10', '--adapter=invalid'])


class TestCLIMain(unittest.TestCase):
    """Integration: main() with mocks and env."""

    def test_missing_upload_path_returns_1(self):
        from services.biomon_ai import cli
        old_env = os.environ.pop('CAMERA_TRAP_UPLOAD_PATH', None)
        try:
            with redirect_stderr(io.StringIO()):
                rc = cli.main(['--batch=5', '--adapter=stub'])
            self.assertEqual(rc, 1)
        finally:
            if old_env is not None:
                os.environ['CAMERA_TRAP_UPLOAD_PATH'] = old_env

    def test_missing_database_url_returns_1(self):
        from services.biomon_ai import cli
        old_db = os.environ.pop('CT_DATABASE_URL', None)
        try:
            with redirect_stderr(io.StringIO()):
                rc = cli.main([
                    '--batch=5', '--adapter=stub',
                    '--upload-path=/tmp/fake',
                ])
            self.assertEqual(rc, 1)
        finally:
            if old_db is not None:
                os.environ['CT_DATABASE_URL'] = old_db


# ════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    unittest.main(verbosity=2)
