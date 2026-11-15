from copy import deepcopy
from collections import defaultdict

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.pam import pam_analytics_calculator as calculator
from app.pam.analytics_inputs import select_species


@pytest.fixture
def baseline():
    return {
        "version": 1,
        "shared": [100, 12345],
        "species": {"1": [12, 34, 0.95, [1, 12]], "2": [56, 78, 0.90, [3, 9]]},
    }


def test_first_run_and_manual_run_include_all_species(baseline):
    assert select_species(baseline, None) == {1: 12, 2: 56}
    assert select_species(baseline, baseline, force=True) == {1: 12, 2: 56}
    assert select_species(baseline, deepcopy(baseline)) == {}


@pytest.mark.parametrize("index,value", [
    (0, 13),
    (1, 35),  # Detection edited without changing the count.
    (2, 0.98),
    (3, [4, 8]),
])
def test_species_only_changes_rebuild_just_that_species(baseline, index, value):
    current = deepcopy(baseline)
    current["species"]["1"][index] = value
    assert select_species(current, baseline) == {1: current["species"]["1"][0]}


def test_last_detection_removed_and_new_species_added(baseline):
    current = deepcopy(baseline)
    del current["species"]["1"]
    current["species"]["3"] = [1, 99, 0.95, [1, 12]]
    assert select_species(current, baseline) == {1: 0, 3: 1}


@pytest.mark.parametrize("key,value", [("version", 2), ("shared", [101, 67890])])
def test_shared_change_rebuilds_all_species(baseline, key, value):
    current = deepcopy(baseline)
    current[key] = value
    assert select_species(current, baseline) == {1: 12, 2: 56}


def test_removed_species_clears_old_outputs_without_touching_other_species(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pam.db'}")
    calculator.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE detections (species_id, recording_id, confidence)"))
        conn.execute(text("CREATE TABLE recordings (recording_id, location_id, datetime_start)"))
        conn.execute(text("CREATE TABLE locations (location_id)"))
        conn.execute(text("CREATE TABLE location_institutions (location_id, institution_id)"))
    monkeypatch.setattr(calculator, "_precalculate_biotope_effort_map", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr(calculator, "_calculate_and_save_biotope_activity", lambda *a, **kw: None)
    with Session(engine) as session:
        for species_id in (1, 2):
            session.add_all([
                calculator.SpeciesYearlyTrends(
                    species_id=species_id, year=2024, institution_id=42, mean_rai=1, lower_ci=1, upper_ci=1
                ),
                calculator.SpeciesBiotopeYearlyActivity(
                    species_id=species_id, biotope_id=1, year=2024, institution_id=42
                ),
                calculator.AnalysisIntermediate(
                    species_id=species_id, location_id=1, year=2024, month=1, month_part=1, day_part=1
                ),
                calculator.AnalyticsLog(
                    species_id=species_id, detection_count=10, last_calculated_at=pd.Timestamp("2024-01-01")
                ),
            ])
        session.commit()
        calculator._run_calculation_cycle(
            session, pd.DataFrame({"count": [0]}, index=[1]),
            trends_only=False, biotopes_only=False, thresholds=defaultdict(lambda: 0.95),
        )
        for model in (
            calculator.SpeciesYearlyTrends, calculator.SpeciesBiotopeYearlyActivity,
            calculator.AnalysisIntermediate, calculator.AnalyticsLog,
        ):
            assert [row.species_id for row in session.query(model).all()] == [2], model.__name__
