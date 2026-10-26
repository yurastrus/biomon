"""
Unit tests for app/pam/service_forecast.py (recorder service forecast).

Pure logic, no DB and no Flask app.

Run:
    venv/Scripts/python -m pytest tests/test_pam_service_forecast.py -v
"""

from datetime import date, datetime, timedelta

import pytest

from app.pam import service_forecast as sf

TODAY = date(2026, 10, 26)


def _dt(days_ago, hour=12):
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour)


def visit(days_ago, purpose=sf.PURPOSE_ROUTINE, hours=6, battery=1, rated=300,
          card=59.6, last_rec_days_ago=None, recorded_hours=0.0):
    return sf.Visit(
        visit_datetime=_dt(days_ago), purpose_id=purpose, hours_per_day=hours,
        battery_type_id=battery, battery_rated_hours=rated, card_capacity_gb=card,
        last_recording=_dt(last_rec_days_ago) if last_rec_days_ago is not None else None,
        recorded_hours=recorded_hours,
    )


def fc(visits, last_recording=None, cal=None, loc=1):
    return sf.forecast_location(loc, visits, last_recording, cal or sf.Calibration(), TODAY)


# ── carry-over of kept batteries and cards ──────────────────────────────────

def test_fresh_battery_uses_rated_capacity_without_history():
    out = fc([visit(10, rated=300, card=None)])
    assert out['battery_days_left'] == 300 / 6 - 10
    assert out['battery_source'] == 'rated'


def test_kept_batteries_carry_over_remaining_charge():
    # 300 h at 6 h/day = 50 days; 30 days used before the visit that kept them,
    # 5 days since: 50 - 30 - 5 = 15 days left (the old code said 200/6 - 5 = 28).
    visits = [visit(35, rated=300, card=None),
              visit(5, battery=sf.BATTERY_KEPT_TYPE_ID, rated=200, card=None)]
    out = fc(visits)
    assert out['battery_days_left'] == 15
    assert out['battery_source'] == 'kept'


def test_no_battery_change_is_treated_as_kept():
    visits = [visit(35, rated=300, card=None), visit(5, battery=None, rated=None, card=None)]
    assert fc(visits)['battery_days_left'] == 15


def test_kept_card_carries_over_used_space():
    # 64 GB card at 6 h/day x 290 MB/h = 1.699 GB/day → 35 days; 20 days used,
    # card kept, 5 more days → ~10 days left (the old code reset it to 35 - 5).
    visits = [visit(25, rated=None, card=59.6),
              visit(5, rated=None, battery=None, card=None)]
    out = fc(visits)
    assert out['sd_card_days_left'] == round(59.6 / (6 * 290 / 1024) - 25)
    assert out['limiting'] == 'sd'


def test_kept_resources_without_history_give_no_forecast():
    out = fc([visit(5, battery=sf.BATTERY_KEPT_TYPE_ID, rated=200, card=None)])
    assert out['status'] == 'unknown'
    assert out['battery_days_left'] is None


def test_removal_resets_carry_over():
    visits = [visit(100, rated=300), visit(60, purpose=sf.PURPOSE_REMOVED, battery=None, card=None),
              visit(10, purpose=sf.PURPOSE_INSTALL, battery=sf.BATTERY_KEPT_TYPE_ID, rated=200, card=None)]
    assert fc(visits)['battery_days_left'] is None


# ── statuses ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('days_ago, status', [
    (10, 'ok'),          # 40 days left
    (40, 'warning'),     # 10 days left
    (48, 'critical'),    # 2 days left
    (60, 'overdue'),     # -10 days
])
def test_status_thresholds(days_ago, status):
    assert fc([visit(days_ago, card=None)])['status'] == status


def test_visit_by_is_exhaustion_date():
    out = fc([visit(10, card=None)])
    assert out['visit_by'] == TODAY + timedelta(days=40)


def test_overdue_counts_lost_days():
    out = fc([visit(60, card=None)])
    assert out['lost_days'] == 10


def test_stopped_when_deployment_data_end_and_forecast_exhausted():
    out = fc([visit(60, card=None, last_rec_days_ago=12)])
    assert out['status'] == 'stopped'
    assert out['lost_days'] == 12
    assert out['last_recording'] == TODAY - timedelta(days=12)


def test_silent_data_with_resource_left_is_not_stopped():
    # Partial upload: data end 10 days ago but the forecast still has 40 days.
    assert fc([visit(10, card=None, last_rec_days_ago=9)])['status'] == 'ok'


def test_removed():
    out = fc([visit(5, purpose=sf.PURPOSE_REMOVED, battery=None, card=None)])
    assert out['status'] == 'removed'


def test_recordings_without_visits_is_no_log():
    assert fc([], last_recording=_dt(30))['status'] == 'no_log'


def test_nothing_at_all_is_inactive():
    assert fc([])['status'] == 'inactive'


def test_old_visit_and_old_data_is_inactive():
    assert fc([visit(250)], last_recording=_dt(240))['status'] == 'inactive'


def test_fresh_install_beats_old_recordings():
    out = fc([visit(16, purpose=sf.PURPOSE_INSTALL)], last_recording=_dt(240))
    assert out['status'] not in ('inactive', 'removed')


# ── calibration ─────────────────────────────────────────────────────────────

def _run(start_days_ago, stop_days_ago, next_days_ago, battery=4, rated=450,
         card=238.4, rec_hours=None):
    """A completed deployment: install, recordings end at stop, next visit."""
    days = start_days_ago - stop_days_ago
    return [visit(start_days_ago, battery=battery, rated=rated, card=card,
                  last_rec_days_ago=stop_days_ago,
                  recorded_hours=rec_hours if rec_hours is not None else days * 5.0),
            visit(next_days_ago, purpose=sf.PURPOSE_REMOVED, battery=None, card=None)]


def test_battery_capacity_is_median_of_runs_that_stopped_early():
    history = {1: _run(300, 240, 200), 2: _run(300, 230, 200), 3: _run(300, 250, 200)}
    cal = sf.calibrate(history)
    hours, n = cal.battery_hours[4]
    assert n == 3
    assert hours == 60 * 6          # median of 50, 60, 70 days at 6 h/day


def test_runs_still_recording_at_next_visit_are_not_samples():
    history = {i: _run(300, 201, 200) for i in range(5)}    # stopped 1 day before the visit
    assert 4 not in sf.calibrate(history).battery_hours


def test_too_few_runs_keep_rated_capacity():
    cal = sf.calibrate({1: _run(300, 240, 200), 2: _run(300, 230, 200)})
    assert 4 not in cal.battery_hours
    v = visit(10, battery=4, rated=450)
    assert cal.battery_capacity(v) == (450.0, 'rated', 0)


def test_card_limited_runs_do_not_calibrate_batteries():
    # 64 GB card filled: 59.6 GB / 290 MB/h = 210 h written.
    history = {i: _run(300, 260, 200, card=59.6, rec_hours=209) for i in range(4)}
    assert 4 not in sf.calibrate(history).battery_hours


def test_data_rate_is_tightest_bound_per_location():
    # 59.6 GB card held 549 h → rate <= 111 MB/h at this recorder.
    history = {9: _run(300, 200, 190, card=59.6, rec_hours=549)}
    cal = sf.calibrate(history)
    assert cal.rate(9) == pytest.approx(59.6 * 1024 / 549)
    assert cal.rate(10) == sf.DEFAULT_DATA_RATE_MB_PER_HOUR   # too few locations to learn a default


def test_data_rate_never_above_default_or_below_floor():
    history = {1: _run(300, 290, 280, card=953.6, rec_hours=10),     # bound far above default
               2: _run(300, 200, 190, card=1.0, rec_hours=1000)}     # absurd label
    cal = sf.calibrate(history)
    assert cal.rate(1) == sf.DEFAULT_DATA_RATE_MB_PER_HOUR
    assert cal.rate(2) == sf.MIN_DATA_RATE_MB_PER_HOUR


def test_default_rate_is_median_of_location_rates():
    history = {i: _run(300, 200, 190, card=59.6, rec_hours=h)
               for i, h in enumerate((300, 400, 500))}
    cal = sf.calibrate(history)
    assert cal.default_rate == pytest.approx(59.6 * 1024 / 400)


def test_calibrated_capacity_used_in_forecast():
    cal = sf.Calibration(battery_hours={4: (360.0, 15)})
    out = fc([visit(10, battery=4, rated=450, card=None)], cal=cal)
    assert out['battery_days_left'] == 50
    assert out['battery_source'] == 'history'
    assert out['battery_runs'] == 15


def test_visits_from_rows_groups_and_sorts():
    class Row:
        def __init__(self, loc, days_ago):
            self.location_id = loc
            self.visit_datetime = _dt(days_ago)
            self.visit_purpose_id = 1
            self.recording_hours_per_day = 6
            self.battery_type_id = 1
            self.estimated_recording_hours = 300
            self.capacity_gb = None
            self.is_operational = None
            self.last_recording = None
            self.recorded_hours = None
    grouped = sf.visits_from_rows([Row(1, 5), Row(2, 3), Row(1, 50)])
    assert [v.visit_datetime for v in grouped[1]] == [_dt(50), _dt(5)]
    assert grouped[1][0].card_capacity_gb is None
    assert grouped[2][0].recorded_hours == 0.0
