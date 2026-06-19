"""
Tests for the human-verified DeepFaune import (level 4) — verification_import.py.

Covers:
  - _series_mode_species: per-series MODE + tie-break (decision A)
  - run_verification_import orchestration (mocked helpers): skip
    completed/archived (KEY), write only pending, species = mode,
    quantity NULL, idempotent re-import (update on conflict)
  - route import_verification_run: access (manager+), validation, verifier
    guard, success path

DB-integration behaviours (real consensus trigger, photo matching) are verified
manually/locally — the CT schema is Postgres-specific and the suite mocks the
ct session, as the other CT tests do.

Run:
    venv/bin/python -m unittest tests.test_ct_verification_import -v
"""

import os
import unittest
from unittest.mock import patch, MagicMock

from app.camera_traps.verification_import import (
    _series_mode_species,
    _group_matched_by_observation,
)


def _row(label, score=None):
    return {'base_label': label, 'base_score': score}


# ── pure logic: series mode + tie-break (decision A) ────────────────────────
class TestSeriesModeSpecies(unittest.TestCase):
    LABEL_MAP = {'roe deer': 4, 'fox': 7, 'empty': -1, 'human': -5}

    def _mode(self, rows):
        # rows: list of (label, score) → list of (row_dict, photo_id)
        pairs = [(_row(l, s), i) for i, (l, s) in enumerate(rows)]
        return _series_mode_species(pairs, self.LABEL_MAP)

    def test_simple_majority(self):
        sid, amb = self._mode([('roe deer', 0.9), ('roe deer', 0.8), ('fox', 0.7)])
        self.assertEqual(sid, 4)
        self.assertFalse(amb)

    def test_real_species_beats_special_on_tie(self):
        # 1 vote roe deer (id 4) vs 1 vote empty (id -1) → real species wins.
        sid, amb = self._mode([('roe deer', 0.5), ('empty', 0.99)])
        self.assertEqual(sid, 4)
        self.assertTrue(amb)

    def test_tie_broken_by_summed_score(self):
        # roe deer (id 4, score 0.4) vs fox (id 7, score 0.9), 1 vote each → fox.
        sid, amb = self._mode([('roe deer', 0.4), ('fox', 0.9)])
        self.assertEqual(sid, 7)
        self.assertTrue(amb)

    def test_tie_broken_by_smaller_id_when_scores_equal(self):
        sid, _ = self._mode([('fox', 0.5), ('roe deer', 0.5)])
        self.assertEqual(sid, 4)  # 4 < 7

    def test_unmapped_labels_yield_none(self):
        sid, amb = self._mode([('unicorn', 0.9), ('dragon', 0.8)])
        self.assertIsNone(sid)
        self.assertFalse(amb)

    def test_unmapped_rows_are_ignored_in_vote(self):
        # 'unicorn' not in map → ignored; fox is the only real vote.
        sid, _ = self._mode([('unicorn', 0.9), ('fox', 0.3)])
        self.assertEqual(sid, 7)

    def test_grouping_by_observation(self):
        matched = [('r1', 101, 10), ('r2', 102, 10), ('r3', 201, 20)]
        by_obs = _group_matched_by_observation(matched)
        self.assertEqual(set(by_obs), {10, 20})
        self.assertEqual(len(by_obs[10]), 2)
        self.assertEqual(by_obs[10][0], ('r1', 101))


# ── orchestration: skip / quantity NULL / mode / idempotency ────────────────
class TestRunOrchestration(unittest.TestCase):
    """run_verification_import with module helpers patched (no DB / no app)."""

    def _make_photo(self, pid):
        p = MagicMock()
        p.id = pid
        p.identification_count = 0
        return p

    def _run(self, existing_list=None):
        # existing_list: per-photo return values for query(Identification).first()
        # (one entry per photo of the pending series). Default: all new (None).
        if existing_list is None:
            existing_list = [None, None]
        from app.camera_traps import verification_import as vi

        obs_pending = MagicMock(id=10, status='pending')
        obs_pending.photos = [self._make_photo(101), self._make_photo(102)]
        obs_done = MagicMock(id=20, status='completed')
        obs_done.photos = [self._make_photo(201)]

        # row_a/row_b → roe deer (id 4); row_c (completed series) → fox
        matched = [
            (_row('roe deer', 0.9), 101, 10),
            (_row('roe deer', 0.8), 102, 10),
            (_row('fox', 0.7), 201, 20),
        ]

        added = []
        session = MagicMock()
        session.add.side_effect = lambda obj: added.append(obj)

        # Shared iterator so successive per-photo Identification lookups return
        # successive entries (a fresh mock q per call would otherwise reset it).
        ident_iter = iter(existing_list)

        def query_side_effect(model):
            from app.camera_traps.models import Observation, Identification
            q = MagicMock()
            if model is Observation:
                q.filter.return_value.all.return_value = [obs_pending]  # only pending fetched
            elif model is Identification:
                q.filter_by.return_value.first.side_effect = \
                    lambda *a, **k: next(ident_iter, None)
            return q
        session.query.side_effect = query_side_effect

        with patch.object(vi, 'load_label_map', return_value={'roe deer': 4, 'fox': 7}), \
             patch.object(vi, '_load_location_photo_index', return_value={}), \
             patch.object(vi, '_match', return_value=(matched, [], [])), \
             patch.object(vi, '_statuses_for', return_value={10: 'pending', 20: 'completed'}), \
             patch.object(vi, 'check_consensus_for_observation') as mock_consensus:
            report = vi.run_verification_import(session, location_id=1, rows=[], verifier_user_id=5)
        return report, added, mock_consensus

    def test_skips_completed_series_and_writes_pending(self):
        report, added, mock_consensus = self._run()
        # obs 20 (completed) skipped; obs 10 (pending) written.
        self.assertEqual(report['skipped_consensus_series'], 1)
        self.assertEqual(report['series_written'], 1)
        # consensus checked only for the pending series.
        mock_consensus.assert_called_once()
        self.assertEqual(mock_consensus.call_args.args[0], 10)

    def test_writes_mode_species_with_null_quantity_on_all_photos(self):
        report, added, _ = self._run()
        # 2 photos of the pending series → 2 new identifications.
        self.assertEqual(report['identifications_added'], 2)
        self.assertEqual(len(added), 2)
        for ident in added:
            self.assertEqual(ident.species_id, 4)      # mode = roe deer
            self.assertIsNone(ident.quantity)          # decision C: NULL
            self.assertEqual(ident.user_id, 5)         # credited to verifier

    def test_idempotent_reimport_updates_existing(self):
        e1, e2 = MagicMock(), MagicMock()
        e1.species_id = e2.species_id = 99
        report, added, _ = self._run(existing_list=[e1, e2])
        # No new rows added; existing identifications updated to the mode species.
        self.assertEqual(report['identifications_added'], 0)
        self.assertEqual(report['identifications_updated'], 2)
        self.assertEqual(e1.species_id, 4)
        self.assertEqual(e2.species_id, 4)


# ── route: access / validation / verifier guard / success ───────────────────
class CtVerificationRouteBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine', return_value=MagicMock())
        cls._ct_patcher.start()
        from app import create_app
        cls.app = create_app('testing')
        cls.app.config['GEOSERVER_URL'] = 'http://test-geoserver'

    @classmethod
    def tearDownClass(cls):
        cls._ct_patcher.stop()
        os.environ.pop('DATABASE_URL', None)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app.extensions import db
        db.create_all()
        self._seed()
        self.client = self.app.test_client()

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self):
        from app.extensions import db, bcrypt
        from app.models import User, Role
        role_manager = Role(name='manager')
        role_verifier = Role(name='ct_verifier')
        role_viewer = Role(name='viewer')
        db.session.add_all([role_manager, role_verifier, role_viewer])
        db.session.flush()
        pw = bcrypt.generate_password_hash('pw').decode('utf-8')
        self.manager = User(username='mgr', password_hash=pw)
        self.manager.roles.append(role_manager)
        self.verifier = User(username='ver', password_hash=pw)
        self.verifier.roles.append(role_verifier)
        self.viewer = User(username='viw', password_hash=pw)
        self.viewer.roles.append(role_viewer)
        db.session.add_all([self.manager, self.verifier, self.viewer])
        db.session.commit()

    def _login(self, uid):
        with self.client.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True

    def _post(self, data, uid):
        self._login(uid)
        return self.client.post(
            '/uk/camera-traps/import-classification/verification/run',
            data=data, headers={'X-CSRFToken': 'test'})


class TestVerificationRunRoute(CtVerificationRouteBase):

    def test_viewer_forbidden_redirect(self):
        resp = self._post({'location_id': 1, 'verifier_id': 1}, self.viewer.id)
        self.assertEqual(resp.status_code, 302)  # role_required redirects

    def test_missing_verifier_400(self):
        resp = self._post({'location_id': 5}, self.manager.id)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_verifier_rejected(self):
        # viewer has no identify right → not a valid verifier.
        resp = self._post({'location_id': 5, 'verifier_id': self.viewer.id}, self.manager.id)
        self.assertEqual(resp.status_code, 400)

    def test_success_calls_import_and_commits(self):
        fake_report = {'series_written': 3, 'identifications_added': 9,
                       'identifications_updated': 0, 'skipped_consensus_series': 1,
                       'no_species_series': 0, 'consensus_reached': 2,
                       'matched_photos': 9, 'csv_unmatched': 0}
        with patch('app.camera_traps.routes.get_ct_session', return_value=MagicMock()), \
             patch('app.camera_traps.routes.close_ct_session'), \
             patch('app.camera_traps.routes._read_uploaded_csv', return_value=([{'x': 1}], [])), \
             patch('app.camera_traps.verification_import.run_verification_import',
                   return_value=fake_report) as mock_run:
            resp = self._post({'location_id': 5, 'verifier_id': self.verifier.id}, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['series_written'], 3)
        mock_run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
