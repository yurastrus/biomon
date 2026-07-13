# SPDX-License-Identifier: AGPL-3.0-only
"""
Tests for analytics_calculator._calculate_mann_kendall() and _mk_test_for_scope().

_calculate_mann_kendall is pure (no DB access), so these tests need no DB mocks,
mirroring tests/test_analytics.py.

Run:
    venv/Scripts/python -m pytest tests/test_mann_kendall.py -v
"""
import unittest


class TestCalculateMannKendall(unittest.TestCase):

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _mk(self, years, values, **kw):
        from app.camera_traps.analytics_calculator import _calculate_mann_kendall
        return _calculate_mann_kendall(years, values, **kw)

    # --- insufficient-data guard (the automatic protection) ---

    def test_too_few_years_returns_none(self):
        # 4 years < MK_MIN_YEARS (5) → no result, caller stores nothing.
        # (At n=4 the exact test can't reach p<0.05 anyway.)
        self.assertIsNone(self._mk([2020, 2021, 2022, 2023], [1.0, 2.0, 3.0, 4.0]))

    def test_single_year_returns_none(self):
        self.assertIsNone(self._mk([2020], [5.0]))

    def test_empty_returns_none(self):
        self.assertIsNone(self._mk([], []))

    def test_custom_min_years_threshold(self):
        # With min_years=3, a 3-point series is now accepted.
        result = self._mk([2020, 2021, 2022], [1.0, 2.0, 3.0], min_years=3)
        self.assertIsNotNone(result)
        self.assertEqual(result['n_years'], 3)

    def test_none_values_dropped_below_threshold(self):
        # One None pair drops the usable series to 4 → below default guard (5).
        self.assertIsNone(
            self._mk([2020, 2021, 2022, 2023, 2024], [1.0, 2.0, None, 4.0, 5.0]))

    # --- trend direction ---

    def test_monotonic_increasing(self):
        result = self._mk([2018, 2019, 2020, 2021, 2022], [1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(result['trend'], 'increasing')
        self.assertAlmostEqual(result['mk_tau'], 1.0, places=6)
        self.assertLess(result['mk_p'], 0.05)
        self.assertGreater(result['sen_slope'], 0)

    def test_monotonic_decreasing(self):
        result = self._mk([2018, 2019, 2020, 2021, 2022], [5.0, 4.0, 3.0, 2.0, 1.0])
        self.assertEqual(result['trend'], 'decreasing')
        self.assertAlmostEqual(result['mk_tau'], -1.0, places=6)
        self.assertLess(result['mk_p'], 0.05)
        self.assertLess(result['sen_slope'], 0)

    def test_no_significant_trend(self):
        # Non-monotonic zig-zag → not significant.
        result = self._mk([2018, 2019, 2020, 2021, 2022], [3.0, 1.0, 4.0, 1.0, 3.0])
        self.assertEqual(result['trend'], 'no_trend')

    # --- edge cases ---

    def test_flat_series_is_no_trend_not_crash(self):
        # All identical → degenerate tau/p (NaN). Must be handled as no_trend.
        result = self._mk([2018, 2019, 2020, 2021, 2022], [2.0, 2.0, 2.0, 2.0, 2.0])
        self.assertEqual(result['trend'], 'no_trend')
        self.assertEqual(result['mk_p'], 1.0)

    def test_ties_do_not_crash(self):
        result = self._mk([2018, 2019, 2020, 2021, 2022], [1.0, 1.0, 2.0, 2.0, 3.0])
        self.assertIn(result['trend'], ('increasing', 'no_trend'))
        self.assertIsNotNone(result['mk_tau'])

    def test_year_gaps_allowed(self):
        # Non-contiguous years still form a valid increasing series.
        result = self._mk([2010, 2013, 2017, 2020, 2024], [1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(result['trend'], 'increasing')

    def test_unsorted_input_is_sorted_by_year(self):
        result = self._mk([2022, 2018, 2020, 2019, 2021], [5.0, 1.0, 3.0, 2.0, 4.0])
        self.assertEqual(result['trend'], 'increasing')

    def test_result_keys(self):
        result = self._mk([2018, 2019, 2020, 2021, 2022], [1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(
            set(result.keys()),
            {'n_years', 'mk_tau', 'mk_p', 'trend', 'sen_slope'},
        )
        self.assertEqual(result['n_years'], 5)


class TestMkTestForScope(unittest.TestCase):

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _trend_objs(self, series):
        from app.camera_traps.models import SpeciesYearlyTrend
        return [
            SpeciesYearlyTrend(
                species_id=1, year=y, scope_type='global', scope_id='',
                mean_dr_index=v, lower_ci=v, upper_ci=v,
            )
            for y, v in series
        ]

    def test_builds_trend_test_object(self):
        from app.camera_traps.analytics_calculator import _mk_test_for_scope
        from app.camera_traps.models import SpeciesTrendTest
        objs = self._trend_objs(
            [(2018, 1.0), (2019, 2.0), (2020, 3.0), (2021, 4.0), (2022, 5.0)])
        result = _mk_test_for_scope(1, 'global', '', objs)
        self.assertIsInstance(result, SpeciesTrendTest)
        self.assertEqual(result.trend, 'increasing')
        self.assertEqual(result.species_id, 1)
        self.assertEqual(result.scope_type, 'global')

    def test_insufficient_years_returns_none(self):
        from app.camera_traps.analytics_calculator import _mk_test_for_scope
        objs = self._trend_objs([(2019, 1.0), (2020, 2.0), (2021, 3.0)])
        self.assertIsNone(_mk_test_for_scope(1, 'global', '', objs))

    def test_empty_returns_none(self):
        from app.camera_traps.analytics_calculator import _mk_test_for_scope
        self.assertIsNone(_mk_test_for_scope(1, 'global', '', []))


if __name__ == '__main__':
    unittest.main()
