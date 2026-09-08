"""
Verifier expertise page (/camera-traps/expertise).

Covers:
  - the consensus helpers (final_species, loo_species) against the canonical
    rule: most distinct voters, ties broken by the largest quantity;
  - leave-one-out, the anti-circularity core of the page: a series where the
    evaluated person's own vote decided the outcome must NOT count as a hit for
    them, and a series with no independent remainder must drop out entirely;
  - the date window narrowing which votes are evaluated WITHOUT changing how a
    series was decided;
  - service categories (species_id < 0) excluded by default;
  - both grouping modes — by verifier and by species — and the narrowing filter
    of the opposite axis;
  - Wilson interval and row building (min_n, ordering);
  - access: admin only, plus the hub card shown to admins only.
"""

import contextlib
import os
import unittest
from collections import namedtuple
from datetime import datetime
from unittest.mock import patch, MagicMock

from app.camera_traps.routes import (
    final_species, loo_species, wilson_interval,
    compute_expertise_stats, build_expertise_rows,
)

# Mirrors a row of fetch_expertise_votes().
Vote = namedtuple('Vote', 'obs_id user_id species_id quantity voted_at observed_at')

DAY = datetime(2026, 3, 1, 12, 0)    # when the identification was made
SHOT = datetime(2026, 2, 1, 3, 0)    # when the series was photographed
DEER, ROE, FOX = 10, 11, 12
EMPTY = -1  # service category ("empty frame")


def v(obs, user, species, quantity=1, at=DAY, shot=SHOT):
    return Vote(obs, user, species, quantity, at, shot)


# ─────────────────────────────────────────────────────────────────────────────
# Consensus helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalSpecies(unittest.TestCase):

    def test_unanimous_two_voters(self):
        votes = [(1, ROE, 1), (2, ROE, 1)]
        self.assertEqual(final_species(votes), ROE)

    def test_majority_wins(self):
        votes = [(1, ROE, 1), (2, ROE, 1), (3, DEER, 1)]
        self.assertEqual(final_species(votes), ROE)

    def test_tie_broken_by_largest_quantity(self):
        """Canonical rule: equal votes -> the larger reported quantity wins."""
        votes = [(1, ROE, 1), (2, DEER, 5)]
        self.assertEqual(final_species(votes), DEER)

    def test_no_votes_gives_none(self):
        self.assertIsNone(final_species([]))

    def test_null_species_ignored(self):
        votes = [(1, None, 1), (2, ROE, 1)]
        self.assertEqual(final_species(votes), ROE)


class TestLooSpecies(unittest.TestCase):

    def test_two_voters_leaves_the_other_persons_call(self):
        votes = [(1, ROE, 1), (2, DEER, 1)]
        self.assertEqual(loo_species(votes, user_id=1), DEER)
        self.assertEqual(loo_species(votes, user_id=2), ROE)

    def test_single_voter_series_is_undecidable(self):
        self.assertIsNone(loo_species([(1, ROE, 1)], user_id=1))

    def test_remainder_tie_is_undecidable(self):
        """The evaluated person's vote was the tiebreaker -> nothing to check
        them against, the series must drop out rather than be guessed."""
        votes = [(1, ROE, 1), (2, ROE, 1), (3, DEER, 1)]
        self.assertIsNone(loo_species(votes, user_id=1))
        self.assertIsNone(loo_species(votes, user_id=2))

    def test_remainder_tie_resolved_by_quantity_is_decidable(self):
        votes = [(1, FOX, 1), (2, ROE, 1), (3, DEER, 9)]
        self.assertEqual(loo_species(votes, user_id=1), DEER)

    def test_minority_voter_sees_the_majority(self):
        votes = [(1, DEER, 1), (2, ROE, 1), (3, ROE, 1)]
        self.assertEqual(loo_species(votes, user_id=1), ROE)


# ─────────────────────────────────────────────────────────────────────────────
# compute_expertise_stats — verifier mode
# ─────────────────────────────────────────────────────────────────────────────

class TestExpertiseUsersMode(unittest.TestCase):

    def test_agreeing_pair_scores_full_on_both_metrics(self):
        rows = [v(1, 100, ROE), v(1, 200, ROE)]
        stats, _ = compute_expertise_stats(rows)
        for uid in (100, 200):
            self.assertEqual(stats[uid]['n'], 1)
            self.assertEqual(stats[uid]['hits'], 1)
            self.assertEqual(stats[uid]['loo_n'], 1)
            self.assertEqual(stats[uid]['loo_hits'], 1)

    def test_disagreeing_pair_splits_raw_but_neither_scores_loo(self):
        """Quantity decides the final species; each verifier is checked against
        the other one, so both are wrong under LOO while raw agreement credits
        the winner."""
        rows = [v(1, 100, ROE, quantity=1), v(1, 200, DEER, quantity=5)]
        stats, _ = compute_expertise_stats(rows)
        self.assertEqual(stats[200]['hits'], 1)   # matched the final call
        self.assertEqual(stats[100]['hits'], 0)
        self.assertEqual(stats[100]['loo_hits'], 0)
        self.assertEqual(stats[200]['loo_hits'], 0)

    def test_own_vote_cannot_confirm_itself(self):
        """The core of the page. Two people say ROE, one says DEER: ROE wins.
        Raw agreement credits both ROE voters, but neither has an independent
        remainder (1 vs 1), so their series leaves the LOO denominator."""
        rows = [v(1, 100, ROE), v(1, 200, ROE), v(1, 300, DEER)]
        stats, totals = compute_expertise_stats(rows)

        self.assertEqual(stats[100]['hits'], 1)
        self.assertEqual(stats[100]['loo_n'], 0, 'own vote must not confirm itself')
        self.assertEqual(stats[200]['loo_n'], 0)
        # The dissenter does have an independent remainder — and is wrong.
        self.assertEqual(stats[300]['loo_n'], 1)
        self.assertEqual(stats[300]['loo_hits'], 0)
        self.assertEqual(totals['undecidable'], 2)

    def test_single_vote_series_counts_raw_but_not_loo(self):
        rows = [v(1, 100, ROE)]
        stats, totals = compute_expertise_stats(rows)
        self.assertEqual(stats[100]['n'], 1)
        self.assertEqual(stats[100]['hits'], 1)
        self.assertEqual(stats[100]['loo_n'], 0)
        self.assertEqual(totals['undecidable'], 1)

    def test_series_are_counted_once(self):
        rows = [v(1, 100, ROE), v(1, 200, ROE),
                v(2, 100, DEER), v(2, 200, DEER)]
        stats, totals = compute_expertise_stats(rows)
        self.assertEqual(totals['series'], 2)
        self.assertEqual(stats[100]['n'], 2)


class TestSpecialCategories(unittest.TestCase):

    def test_series_answered_by_a_service_category_are_excluded(self):
        """Agreeing that a frame is empty is easy and would inflate everyone."""
        rows = [v(1, 100, EMPTY), v(1, 200, EMPTY),
                v(2, 100, ROE), v(2, 200, ROE)]
        stats, totals = compute_expertise_stats(rows)
        self.assertEqual(totals['series'], 1)
        self.assertEqual(stats[100]['n'], 1)

    def test_service_categories_included_on_request(self):
        rows = [v(1, 100, EMPTY), v(1, 200, EMPTY),
                v(2, 100, ROE), v(2, 200, ROE)]
        stats, totals = compute_expertise_stats(rows, include_special=True)
        self.assertEqual(totals['series'], 2)
        self.assertEqual(stats[100]['n'], 2)

    def test_calling_an_animal_empty_still_counts_as_a_miss(self):
        """The commonest real disagreement: one says roe, two say empty frame.
        Excluding service categories must hide the empty ROW, never the error."""
        rows = [v(1, 100, EMPTY), v(1, 200, ROE), v(1, 300, ROE)]
        stats, _ = compute_expertise_stats(rows)
        self.assertEqual(stats[100]['n'], 1)
        self.assertEqual(stats[100]['hits'], 0)
        self.assertEqual(stats[100]['loo_n'], 1)
        self.assertEqual(stats[100]['loo_hits'], 0)

    def test_service_votes_still_decide_the_series(self):
        """Two "empty" votes against one "roe" keep the series a non-answer, so
        nobody is graded on it."""
        rows = [v(1, 100, ROE), v(1, 200, EMPTY), v(1, 300, EMPTY)]
        stats, totals = compute_expertise_stats(rows)
        self.assertEqual(totals['series'], 0)
        self.assertEqual(stats, {})

    def test_no_species_row_is_created_for_a_service_category(self):
        rows = [v(1, 100, EMPTY), v(1, 200, ROE), v(1, 300, ROE)]
        stats, _ = compute_expertise_stats(rows, mode='species')
        self.assertEqual(set(stats), {ROE})
        self.assertEqual(stats[ROE]['said'], 2)  # the "empty" call is not a roe claim

    def test_null_species_always_dropped(self):
        rows = [v(1, 100, None), v(1, 200, ROE)]
        stats, _ = compute_expertise_stats(rows, include_special=True)
        self.assertNotIn(100, stats)


class TestDateWindow(unittest.TestCase):
    """The window picks which votes are judged; it must never re-decide a series."""

    OLD = datetime(2025, 1, 10, 9, 0)
    NEW = datetime(2026, 5, 20, 9, 0)

    def _rows(self):
        # Old voter said ROE, new voter said DEER, quantity gives ROE the series.
        return [v(1, 100, ROE, quantity=4, at=self.OLD),
                v(1, 200, DEER, quantity=1, at=self.NEW)]

    def test_out_of_window_vote_is_not_evaluated(self):
        stats, _ = compute_expertise_stats(
            self._rows(),
            start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
        self.assertNotIn(100, stats)
        self.assertIn(200, stats)

    def test_decision_still_uses_votes_outside_the_window(self):
        """The 2026 voter said DEER; the series is ROE only because of the 2025
        vote. Narrowing the window must not turn that vote into a hit."""
        stats, _ = compute_expertise_stats(
            self._rows(),
            start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
        self.assertEqual(stats[200]['n'], 1)
        self.assertEqual(stats[200]['hits'], 0)
        self.assertEqual(stats[200]['loo_hits'], 0)

    def test_full_window_evaluates_everyone(self):
        stats, _ = compute_expertise_stats(
            self._rows(),
            start_dt=datetime(2024, 1, 1), end_dt=datetime(2027, 1, 1))
        self.assertEqual(set(stats), {100, 200})

    def test_timezone_aware_votes_are_comparable(self):
        """`identifications.created_at` is timestamptz on production and comes
        back aware; the window bounds are built from plain dates. Comparing the
        two raises TypeError, which the route swallows into an empty page."""
        from datetime import timezone
        aware = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
        rows = [Vote(1, 100, ROE, 1, aware, SHOT), Vote(1, 200, ROE, 1, aware, SHOT)]
        stats, _ = compute_expertise_stats(
            rows, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
        self.assertEqual(stats[100]['n'], 1)

    def test_timezone_aware_votes_respect_the_window(self):
        from datetime import timezone
        rows = [Vote(1, 100, ROE, 1, datetime(2025, 3, 1, 12, tzinfo=timezone.utc), SHOT),
                Vote(1, 200, ROE, 1, datetime(2026, 3, 1, 12, tzinfo=timezone.utc), SHOT)]
        stats, _ = compute_expertise_stats(
            rows, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
        self.assertEqual(set(stats), {200})


class TestSpeciesFilterUsersMode(unittest.TestCase):

    def _rows(self):
        return [
            # series 1 and 2 are roe deer; 100 gets the second one wrong
            v(1, 100, ROE), v(1, 200, ROE),
            v(2, 100, DEER), v(2, 200, ROE), v(2, 300, ROE),
            # series 3 is red deer, and 100 wrongly calls it roe
            v(3, 100, ROE), v(3, 200, DEER), v(3, 300, DEER),
        ]

    def test_recall_counts_only_series_whose_final_is_the_species(self):
        stats, _ = compute_expertise_stats(self._rows(), species_id=ROE)
        self.assertEqual(stats[100]['n'], 2)          # series 1 and 2
        self.assertEqual(stats[100]['hits'], 1)

    def test_precision_counts_series_where_the_person_named_the_species(self):
        stats, _ = compute_expertise_stats(self._rows(), species_id=ROE)
        # 100 said "roe" on series 1 and 3; only series 1 really is roe.
        self.assertEqual(stats[100]['said'], 2)
        self.assertEqual(stats[100]['said_hits'], 1)

    def test_precision_is_empty_without_a_species_filter(self):
        """Over all species it would repeat recall by definition."""
        stats, _ = compute_expertise_stats(self._rows())
        self.assertEqual(stats[100]['said'], 0)


# ─────────────────────────────────────────────────────────────────────────────
# compute_expertise_stats — species mode
# ─────────────────────────────────────────────────────────────────────────────

class TestExpertiseSpeciesMode(unittest.TestCase):

    def _rows(self):
        return [
            v(1, 100, ROE), v(1, 200, ROE),                  # roe, unanimous
            v(2, 100, ROE), v(2, 200, DEER), v(2, 300, DEER),  # deer, 100 wrong
        ]

    def test_rows_are_keyed_by_species(self):
        stats, _ = compute_expertise_stats(self._rows(), mode='species')
        self.assertEqual(set(stats), {ROE, DEER})

    def test_recall_is_keyed_by_the_final_species(self):
        stats, _ = compute_expertise_stats(self._rows(), mode='species')
        self.assertEqual(stats[ROE]['n'], 2)      # two votes on the roe series
        self.assertEqual(stats[ROE]['hits'], 2)
        self.assertEqual(stats[DEER]['n'], 3)     # three votes on the deer series
        self.assertEqual(stats[DEER]['hits'], 2)

    def test_precision_is_keyed_by_the_species_named(self):
        """The wrong "roe" call on the deer series is a precision miss for roe,
        not for deer."""
        stats, _ = compute_expertise_stats(self._rows(), mode='species')
        self.assertEqual(stats[ROE]['said'], 3)
        self.assertEqual(stats[ROE]['said_hits'], 2)
        self.assertEqual(stats[DEER]['said'], 2)
        self.assertEqual(stats[DEER]['said_hits'], 2)

    def test_verifier_filter_keeps_only_that_persons_votes(self):
        stats, _ = compute_expertise_stats(self._rows(), mode='species', user_id=100)
        self.assertEqual(stats[ROE]['n'], 1)
        self.assertEqual(stats[DEER]['n'], 1)
        self.assertEqual(stats[DEER]['hits'], 0)

    def test_verifier_filter_does_not_change_the_decision(self):
        """Filtering to the person who was wrong must not make them right."""
        stats, _ = compute_expertise_stats(self._rows(), mode='species', user_id=100)
        self.assertEqual(stats[DEER]['loo_n'], 1)
        self.assertEqual(stats[DEER]['loo_hits'], 0)


# ─────────────────────────────────────────────────────────────────────────────
# Presentation helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestWilsonInterval(unittest.TestCase):

    def test_empty_sample_has_no_interval(self):
        self.assertEqual(wilson_interval(0, 0), (None, None))

    def test_perfect_small_sample_is_not_reported_as_certain(self):
        low, high = wilson_interval(5, 5)
        self.assertLess(low, 100.0)
        self.assertLessEqual(high, 100.0)

    def test_interval_narrows_as_the_sample_grows(self):
        small_low, _ = wilson_interval(19, 20)
        large_low, _ = wilson_interval(1900, 2000)
        self.assertLess(small_low, large_low)

    def test_interval_brackets_the_point_estimate(self):
        low, high = wilson_interval(90, 100)
        self.assertLess(low, 90.0)
        self.assertGreater(high, 90.0)


class TestBuildExpertiseRows(unittest.TestCase):

    def _stats(self):
        return {
            1: {'n': 100, 'hits': 99, 'loo_n': 100, 'loo_hits': 90,
                'said': 0, 'said_hits': 0},
            2: {'n': 50, 'hits': 50, 'loo_n': 50, 'loo_hits': 49,
                'said': 0, 'said_hits': 0},
            3: {'n': 2, 'hits': 2, 'loo_n': 0, 'loo_hits': 0,
                'said': 0, 'said_hits': 0},
        }

    def test_sorted_by_loo_share(self):
        rows = build_expertise_rows(self._stats(), lambda k: f'u{k}')
        self.assertEqual([r['key'] for r in rows[:2]], [2, 1])

    def test_tiny_perfect_sample_does_not_outrank_a_large_one(self):
        """Ordering is by the interval's lower bound: six series at 100% must
        not sit above eleven thousand at 97.5%."""
        stats = {
            'tiny': {'n': 6, 'hits': 6, 'loo_n': 6, 'loo_hits': 6,
                     'said': 0, 'said_hits': 0},
            'huge': {'n': 11000, 'hits': 10725, 'loo_n': 11000, 'loo_hits': 10725,
                     'said': 0, 'said_hits': 0},
        }
        rows = build_expertise_rows(stats, str)
        self.assertEqual(rows[0]['key'], 'huge')
        self.assertEqual(rows[0]['loo_pct'], 97.5)
        self.assertEqual(rows[1]['loo_pct'], 100.0)

    def test_rows_without_loo_sink_to_the_bottom(self):
        """100% raw with no independent check must not top the table."""
        rows = build_expertise_rows(self._stats(), lambda k: f'u{k}')
        self.assertEqual(rows[-1]['key'], 3)
        self.assertIsNone(rows[-1]['loo_pct'])

    def test_min_n_drops_small_samples(self):
        rows = build_expertise_rows(self._stats(), lambda k: f'u{k}', min_n=10)
        self.assertEqual({r['key'] for r in rows}, {1, 2})

    def test_percentages_and_misses(self):
        rows = {r['key']: r for r in build_expertise_rows(self._stats(), str)}
        self.assertEqual(rows[1]['loo_pct'], 90.0)
        self.assertEqual(rows[1]['raw_pct'], 99.0)
        self.assertEqual(rows[1]['loo_misses'], 10)

    def test_label_callback_names_the_row(self):
        rows = build_expertise_rows(self._stats(), lambda k: f'name-{k}')
        self.assertTrue(all(r['name'] == f"name-{r['key']}" for r in rows))


# ─────────────────────────────────────────────────────────────────────────────
# Route: access and wiring
# ─────────────────────────────────────────────────────────────────────────────

def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _generic_session():
    """Self-recursive ct_session mock: every chained method returns itself."""
    q = MagicMock()
    for method in ('join', 'outerjoin', 'filter', 'order_by', 'group_by',
                   'having', 'distinct', 'params', 'limit', 'offset',
                   'select_from', 'with_entities', 'options'):
        getattr(q, method).return_value = q
    q.all.return_value = []
    q.scalar.return_value = 0
    q.first.return_value = None
    q.__iter__ = MagicMock(side_effect=lambda: iter([]))
    q.subquery.return_value = MagicMock()

    sess = MagicMock()
    sess.query.return_value = q
    return sess


class ExpertiseRouteBase(unittest.TestCase):

    URL = '/uk/camera-traps/expertise'

    @classmethod
    def setUpClass(cls):
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock(),
        )
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
        self._seed(db)
        self.client = self.app.test_client()
        # The date-range cache is module level; leaving it filled would leak
        # between tests and hide a broken query.
        from app.camera_traps import routes as ct_routes
        ct_routes._expertise_range_cache.update({'data': None, 'timestamp': None})

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _seed(self, db):
        from app.extensions import bcrypt
        from app.models import User, Role

        roles = {name: Role(name=name) for name in ('admin', 'manager', 'ct_verifier')}
        db.session.add_all(roles.values())
        db.session.flush()

        pw = bcrypt.generate_password_hash('test').decode('utf-8')
        self.admin = User(username='admin_u', password_hash=pw)
        self.admin.roles.append(roles['admin'])
        self.manager = User(username='manager_u', password_hash=pw)
        self.manager.roles.append(roles['manager'])
        self.verifier = User(username='verifier_u', password_hash=pw)
        self.verifier.roles.append(roles['ct_verifier'])
        db.session.add_all([self.admin, self.manager, self.verifier])
        db.session.commit()

    def _get(self, url=None, user_id=None, extra_patches=()):
        if user_id:
            _login(self.client, user_id)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch('app.camera_traps.routes.get_ct_session',
                      return_value=_generic_session())
            )
            stack.enter_context(patch('app.camera_traps.routes.close_ct_session'))
            for p in extra_patches:
                stack.enter_context(p)
            return self.client.get(url or self.URL)


class TestExpertiseAccess(ExpertiseRouteBase):

    def test_anonymous_redirects(self):
        self.assertEqual(self._get().status_code, 302)

    def test_verifier_is_refused(self):
        self.assertEqual(self._get(user_id=self.verifier.id).status_code, 302)

    def test_manager_is_refused(self):
        """Admin only: the page names individuals and their error rate."""
        self.assertEqual(self._get(user_id=self.manager.id).status_code, 302)

    def test_admin_gets_the_page(self):
        self.assertEqual(self._get(user_id=self.admin.id).status_code, 200)


class TestExpertiseModes(ExpertiseRouteBase):

    def _captured_kwargs(self, url):
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with patch('app.camera_traps.routes.compute_expertise_stats', spy):
            resp = self._get(url, user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(spy.called, 'compute_expertise_stats не викликано')
        return spy.call_args.kwargs

    def test_default_mode_is_users(self):
        self.assertEqual(self._captured_kwargs(self.URL)['mode'], 'users')

    def test_species_mode_is_accepted(self):
        kwargs = self._captured_kwargs(self.URL + '?mode=species')
        self.assertEqual(kwargs['mode'], 'species')

    def test_unknown_mode_falls_back_to_users(self):
        kwargs = self._captured_kwargs(self.URL + '?mode=nonsense')
        self.assertEqual(kwargs['mode'], 'users')

    def test_species_filter_ignored_in_species_mode(self):
        """Grouping by species and filtering to one species would leave a
        single row; the verifier filter is the meaningful one there."""
        kwargs = self._captured_kwargs(self.URL + '?mode=species&species_id=5&user_id=7')
        self.assertIsNone(kwargs['species_id'])
        self.assertEqual(kwargs['user_id'], 7)

    def test_verifier_filter_ignored_in_users_mode(self):
        kwargs = self._captured_kwargs(self.URL + '?mode=users&species_id=5&user_id=7')
        self.assertEqual(kwargs['species_id'], 5)
        self.assertIsNone(kwargs['user_id'])

    def test_include_special_flag_is_passed_through(self):
        self.assertTrue(self._captured_kwargs(self.URL + '?include_special=1')['include_special'])
        self.assertFalse(self._captured_kwargs(self.URL)['include_special'])


class TestExpertiseDateDefaults(ExpertiseRouteBase):

    RANGES = None  # filled in below, needs `date`

    def _patched_ranges(self):
        from datetime import date
        return patch('app.camera_traps.routes.get_expertise_date_ranges',
                     return_value={'verified': (date(2021, 4, 5), date(2026, 8, 9)),
                                   'observed': (date(2019, 1, 2), date(2026, 7, 21))})

    def test_both_windows_span_the_whole_record_by_default(self):
        """Each axis starts at its own oldest entry and runs to today — today
        rather than the newest entry, so a hand-typed range never looks
        truncated."""
        from datetime import date
        with self._patched_ranges():
            spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
            with patch('app.camera_traps.routes.compute_expertise_stats', spy):
                resp = self._get(user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs['start_dt'].date(), date(2021, 4, 5))
        self.assertEqual(kwargs['obs_start_dt'].date(), date(2019, 1, 2))
        self.assertEqual(kwargs['end_dt'].date(), date.today())
        self.assertEqual(kwargs['obs_end_dt'].date(), date.today())

    def test_the_two_windows_are_independent(self):
        from datetime import date
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with self._patched_ranges(),                 patch('app.camera_traps.routes.compute_expertise_stats', spy):
            resp = self._get(
                self.URL + '?obs_start_date=2023-01-01&obs_end_date=2023-12-31'
                           '&start_date=2026-02-01&end_date=2026-02-28',
                user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs['obs_start_dt'].date(), date(2023, 1, 1))
        self.assertEqual(kwargs['obs_end_dt'].date(), date(2023, 12, 31))
        self.assertEqual(kwargs['start_dt'].date(), date(2026, 2, 1))
        self.assertEqual(kwargs['end_dt'].date(), date(2026, 2, 28))

    def test_narrowing_one_window_leaves_the_other_full(self):
        from datetime import date
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with self._patched_ranges(),                 patch('app.camera_traps.routes.compute_expertise_stats', spy):
            self._get(self.URL + '?obs_start_date=2024-05-01', user_id=self.admin.id)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs['obs_start_dt'].date(), date(2024, 5, 1))
        self.assertEqual(kwargs['start_dt'].date(), date(2021, 4, 5))

    def test_explicit_dates_override_the_default(self):
        from datetime import date
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with patch('app.camera_traps.routes.compute_expertise_stats', spy):
            resp = self._get(self.URL + '?start_date=2026-02-01&end_date=2026-02-28',
                             user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs['start_dt'].date(), date(2026, 2, 1))
        self.assertEqual(kwargs['end_dt'].date(), date(2026, 2, 28))

    def test_reversed_observation_range_is_swapped_not_emptied(self):
        from datetime import date
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with patch('app.camera_traps.routes.compute_expertise_stats', spy):
            self._get(self.URL + '?obs_start_date=2026-05-01&obs_end_date=2026-01-01',
                      user_id=self.admin.id)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs['obs_start_dt'].date(), date(2026, 1, 1))
        self.assertEqual(kwargs['obs_end_dt'].date(), date(2026, 5, 1))

    def test_reversed_range_is_swapped_not_emptied(self):
        from datetime import date
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with patch('app.camera_traps.routes.compute_expertise_stats', spy):
            self._get(self.URL + '?start_date=2026-05-01&end_date=2026-01-01',
                      user_id=self.admin.id)
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs['start_dt'].date(), date(2026, 1, 1))
        self.assertEqual(kwargs['end_dt'].date(), date(2026, 5, 1))

    def test_garbage_dates_fall_back_to_the_full_period(self):
        from datetime import date
        with patch('app.camera_traps.routes.get_expertise_date_ranges',
                   return_value={'verified': (date(2021, 4, 5), date(2026, 8, 9)),
                                 'observed': (date(2019, 1, 2), date(2026, 7, 21))}):
            spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
            with patch('app.camera_traps.routes.compute_expertise_stats', spy):
                resp = self._get(self.URL + '?start_date=yesterday', user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(spy.call_args.kwargs['start_dt'].date(), date(2021, 4, 5))

    def test_date_range_is_cached(self):
        """A full scan of identifications must not run on every request."""
        from app.camera_traps import routes as ct_routes
        sess = _generic_session()
        sess.query.return_value.first.return_value = None
        first = ct_routes.get_expertise_date_ranges(sess)
        calls = sess.query.call_count
        second = ct_routes.get_expertise_date_ranges(sess)
        self.assertEqual(first, second)
        self.assertEqual(sess.query.call_count, calls, 'кеш не спрацював')


class TestExpertiseHubCard(ExpertiseRouteBase):

    HUB = '/uk/camera-traps/'

    def _hub(self, user_id=None):
        return self._get(self.HUB, user_id=user_id)

    def test_card_visible_to_admin(self):
        resp = self._hub(user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('/camera-traps/expertise', resp.get_data(as_text=True))

    def test_card_hidden_from_non_admin(self):
        resp = self._hub(user_id=self.verifier.id)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('/camera-traps/expertise', resp.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()


class TestExpertiseDateInputs(ExpertiseRouteBase):
    """The date fields must not cap what can be typed.

    `min`/`max` on the inputs made the browser refuse anything before the first
    verification, which reads as a broken control rather than as "there is no
    data there" — especially since the bound is the start of verification
    activity, not the start of the photo record.
    """

    def _html(self):
        from datetime import date
        with patch('app.camera_traps.routes.get_expertise_date_ranges',
                   return_value={'verified': (date(2025, 8, 20), date(2026, 9, 8)),
                                 'observed': (date(2020, 3, 4), date(2026, 7, 21))}):
            resp = self._get(user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_date_inputs_are_not_capped(self):
        html = self._html()
        for field in ('start-date', 'end-date'):
            tag = html.split(f'id="{field}"', 1)[1].split('>', 1)[0]
            self.assertNotIn('min=', tag)
            self.assertNotIn('max=', tag)

    def test_available_range_is_shown_as_a_hint(self):
        html = self._html()
        self.assertIn('2025-08-20', html)
        self.assertIn('2026-09-08', html)

    def test_dates_before_the_first_verification_are_accepted(self):
        from datetime import date
        spy = MagicMock(return_value=({}, {'series': 0, 'undecidable': 0}))
        with patch('app.camera_traps.routes.compute_expertise_stats', spy):
            resp = self._get(self.URL + '?start_date=2015-01-01&end_date=2026-09-08',
                             user_id=self.admin.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(spy.call_args.kwargs['start_dt'].date(), date(2015, 1, 1))


class TestObservationWindow(unittest.TestCase):
    """The capture-time window selects SERIES, not individual votes.

    A series is in the sample or out of it as a whole, and its decision is
    always computed from every vote it ever received — otherwise the same
    series would resolve to different species under different filters.
    """

    OLD_SHOT = datetime(2022, 6, 1, 5, 0)
    NEW_SHOT = datetime(2026, 6, 1, 5, 0)

    def _rows(self):
        return [
            v(1, 100, ROE, at=DAY, shot=self.OLD_SHOT),
            v(1, 200, ROE, at=DAY, shot=self.OLD_SHOT),
            v(2, 100, DEER, at=DAY, shot=self.NEW_SHOT),
            v(2, 200, DEER, at=DAY, shot=self.NEW_SHOT),
        ]

    def test_window_selects_series_by_capture_time(self):
        stats, totals = compute_expertise_stats(
            self._rows(),
            obs_start_dt=datetime(2026, 1, 1), obs_end_dt=datetime(2026, 12, 31))
        self.assertEqual(totals['series'], 1)
        self.assertEqual(stats[100]['n'], 1)

    def test_full_window_keeps_every_series(self):
        stats, totals = compute_expertise_stats(
            self._rows(),
            obs_start_dt=datetime(2020, 1, 1), obs_end_dt=datetime(2027, 1, 1))
        self.assertEqual(totals['series'], 2)
        self.assertEqual(stats[100]['n'], 2)

    def test_decision_keeps_votes_made_outside_the_verification_window(self):
        """A series shot in 2026 but decided by a 2022 vote must not change its
        species just because the verification window excludes that vote."""
        rows = [Vote(1, 100, ROE, 4, datetime(2022, 1, 1), self.NEW_SHOT),
                Vote(1, 200, DEER, 1, datetime(2026, 3, 1), self.NEW_SHOT)]
        stats, _ = compute_expertise_stats(
            rows,
            start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31),
            obs_start_dt=datetime(2026, 1, 1), obs_end_dt=datetime(2026, 12, 31))
        self.assertEqual(set(stats), {200})
        self.assertEqual(stats[200]['hits'], 0)   # ROE won on quantity

    def test_the_two_windows_compose(self):
        rows = [
            v(1, 100, ROE, at=datetime(2025, 9, 1), shot=self.OLD_SHOT),
            v(1, 200, ROE, at=datetime(2025, 9, 1), shot=self.OLD_SHOT),
            v(2, 100, ROE, at=datetime(2026, 9, 1), shot=self.NEW_SHOT),
            v(2, 200, ROE, at=datetime(2026, 9, 1), shot=self.NEW_SHOT),
        ]
        # Old shots only, but verified in 2026 only -> nothing qualifies.
        stats, totals = compute_expertise_stats(
            rows,
            start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31),
            obs_start_dt=datetime(2022, 1, 1), obs_end_dt=datetime(2022, 12, 31))
        self.assertEqual(totals['series'], 1)   # the series is in the sample
        self.assertEqual(stats, {})             # but nobody voted in the window

    def test_timezone_aware_capture_time_is_comparable(self):
        from datetime import timezone
        rows = [Vote(1, 100, ROE, 1, DAY, datetime(2026, 6, 1, tzinfo=timezone.utc)),
                Vote(1, 200, ROE, 1, DAY, datetime(2026, 6, 1, tzinfo=timezone.utc))]
        stats, _ = compute_expertise_stats(
            rows,
            obs_start_dt=datetime(2026, 1, 1), obs_end_dt=datetime(2026, 12, 31))
        self.assertEqual(stats[100]['n'], 1)
