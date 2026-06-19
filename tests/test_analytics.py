"""
Tests for analytics_calculator._run_bootstrap()

The function is pure (no DB access), so these tests need no DB mocks.

Run:
    venv/Scripts/python -m unittest tests.test_analytics -v
"""
import unittest


class TestRunBootstrap(unittest.TestCase):

    def setUp(self):
        from app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _run(self, location_data, scope_locations, all_years,
             scope_type='global', scope_id='', n=200):
        from app.camera_traps.analytics_calculator import _run_bootstrap
        return _run_bootstrap(
            species_id=1,
            location_data=location_data,
            scope_locations=scope_locations,
            all_years=all_years,
            scope_type=scope_type,
            scope_id=scope_id,
            N_ITERATIONS=n,
        )

    # --- basic edge cases ---

    def test_empty_locations_returns_empty(self):
        result = self._run({}, [], [2020, 2021])
        self.assertEqual(result, [])

    def test_empty_years_returns_empty(self):
        result = self._run({1: {2020: (10, 100)}}, [1], [])
        self.assertEqual(result, [])

    def test_all_zero_trap_days_returns_empty(self):
        """If trap_days = 0 for all locations and years, DR is not computed."""
        loc_data = {1: {2020: (0, 0)}}
        result = self._run(loc_data, [1], [2020])
        self.assertEqual(result, [])

    # --- result structure ---

    def test_returns_species_yearly_trend_objects(self):
        from app.camera_traps.models import SpeciesYearlyTrend
        loc_data = {1: {2020: (10, 100), 2021: (15, 200)}}
        result = self._run(loc_data, [1], [2020, 2021])
        self.assertTrue(all(isinstance(t, SpeciesYearlyTrend) for t in result))

    def test_one_result_per_year_with_data(self):
        loc_data = {1: {2020: (10, 100), 2021: (20, 200), 2022: (5, 50)}}
        result = self._run(loc_data, [1], [2020, 2021, 2022])
        years = [t.year for t in result]
        self.assertEqual(sorted(years), [2020, 2021, 2022])

    def test_species_id_is_set(self):
        loc_data = {1: {2020: (10, 100)}}
        result = self._run(loc_data, [1], [2020])
        self.assertTrue(all(t.species_id == 1 for t in result))

    # --- scope fields ---

    def test_global_scope_fields(self):
        loc_data = {1: {2020: (10, 100)}}
        result = self._run(loc_data, [1], [2020], scope_type='global', scope_id='')
        self.assertTrue(all(t.scope_type == 'global' for t in result))
        self.assertTrue(all(t.scope_id == '' for t in result))

    def test_institution_scope_fields(self):
        loc_data = {1: {2020: (10, 100)}}
        result = self._run(loc_data, [1], [2020], scope_type='institution', scope_id='42')
        self.assertTrue(all(t.scope_type == 'institution' for t in result))
        self.assertTrue(all(t.scope_id == '42' for t in result))

    def test_ecoregion_scope_fields(self):
        loc_data = {1: {2020: (10, 100)}}
        result = self._run(loc_data, [1], [2020], scope_type='ecoregion', scope_id='Карпати')
        self.assertTrue(all(t.scope_type == 'ecoregion' for t in result))
        self.assertTrue(all(t.scope_id == 'Карпати' for t in result))

    # --- DR values and confidence intervals ---

    def test_dr_index_in_range_0_to_100(self):
        """DR = (detections×100)/trap_days; with detections ≤ trap_days it must be ≤ 100."""
        loc_data = {1: {2020: (30, 100)}}  # DR ≈ 30
        result = self._run(loc_data, [1], [2020], n=500)
        self.assertEqual(len(result), 1)
        t = result[0]
        self.assertGreater(float(t.mean_dr_index), 0)
        self.assertLessEqual(float(t.mean_dr_index), 100)

    def test_ci_bounds_are_ordered(self):
        """lower_ci ≤ mean_dr_index ≤ upper_ci."""
        loc_data = {1: {2020: (40, 100)}, 2: {2020: (20, 100)}}
        result = self._run(loc_data, [1, 2], [2020], n=500)
        t = result[0]
        self.assertLessEqual(float(t.lower_ci), float(t.mean_dr_index))
        self.assertLessEqual(float(t.mean_dr_index), float(t.upper_ci))

    # --- scope_locations filtering ---

    def test_location_not_in_scope_is_excluded(self):
        """Location 2 with zero detections lowers DR when included."""
        loc_data = {
            1: {2020: (50, 100)},  # DR = 50 (in scope)
            2: {2020: (0, 100)},   # DR = 0  (out of scope in the first test)
        }
        result_scope_1 = self._run(loc_data, [1], [2020], n=500)
        result_scope_12 = self._run(loc_data, [1, 2], [2020], n=500)
        mean_1 = float(result_scope_1[0].mean_dr_index)
        mean_12 = float(result_scope_12[0].mean_dr_index)
        # Including a location with zero DR lowers the mean
        self.assertGreater(mean_1, mean_12)

    def test_location_with_no_data_for_year_counts_as_zero(self):
        """If a location has no data for a year, detections = 0 (zero affects the result)."""
        loc_data = {
            1: {2020: (50, 100)},
            2: {},                  # no data at all
        }
        result = self._run(loc_data, [1, 2], [2020], n=300)
        # Should return a result (non-empty)
        self.assertGreater(len(result), 0)
        # DR should be lower than with location 1 alone
        result_only_1 = self._run(loc_data, [1], [2020], n=300)
        mean_both = float(result[0].mean_dr_index)
        mean_only_1 = float(result_only_1[0].mean_dr_index)
        self.assertLessEqual(mean_both, mean_only_1)

    # --- stochastic stability ---

    def test_more_iterations_narrows_ci(self):
        """More iterations narrow the CI (relative check)."""
        loc_data = {i: {2020: (i * 5, 100)} for i in range(1, 6)}
        locations = list(loc_data.keys())
        result_100 = self._run(loc_data, locations, [2020], n=100)
        result_2000 = self._run(loc_data, locations, [2020], n=2000)
        ci_100 = float(result_100[0].upper_ci) - float(result_100[0].lower_ci)
        ci_2000 = float(result_2000[0].upper_ci) - float(result_2000[0].lower_ci)
        # With more iterations the CI is usually narrower
        self.assertGreater(ci_100, ci_2000 * 0.5)  # soft check


if __name__ == '__main__':
    unittest.main(verbosity=2)
