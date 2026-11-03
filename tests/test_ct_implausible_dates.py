"""Photos with the 1900 placeholder date (no EXIF) stay out of CT analytics.

Regression for the "1900" start year on /analysis/species-dashboard: three
verified series without EXIF reached location_monthly_activity and from
there species_yearly_trends.
"""
from datetime import date, datetime
from unittest.mock import patch

from app.camera_traps.models import Identification, LocationMonthlyActivity
from app.camera_traps.validity import MIN_VALID_CAPTURE_YEAR, plausible_years


def test_plausible_years_drops_placeholder_and_future():
    this_year = date.today().year
    years = [1900, 2000, MIN_VALID_CAPTURE_YEAR, 2024, this_year, this_year + 1, None]
    assert plausible_years(years) == [MIN_VALID_CAPTURE_YEAR, 2024, this_year]


def test_monthly_activity_skips_placeholder_photos(ct_session, make_ct_species,
                                                    make_ct_location, make_ct_observation,
                                                    make_ct_photo):
    LocationMonthlyActivity.__table__.create(bind=ct_session.get_bind(), checkfirst=True)
    from app.camera_traps import analytics_calculator as ac

    fox = make_ct_species()
    loc = make_ct_location()
    good = make_ct_observation(location=loc, status='completed')
    bad = make_ct_observation(location=loc, status='archived')
    for obs, when in ((good, datetime(2025, 6, 1, 12, 0)), (bad, datetime(1900, 1, 1, 0, 0, 1))):
        photo = make_ct_photo(observation=obs, captured_at=when)
        ct_session.add(Identification(photo_id=photo.id, user_id=1, species_id=fox.id))
    ct_session.commit()

    with patch.object(ac, 'get_ct_session', return_value=ct_session), \
         patch.object(ac, 'close_ct_session'):
        assert ac._calculate_monthly_activity(exclude_invalid_locations=False)

    years = {r.year for r in ct_session.query(LocationMonthlyActivity).all()}
    assert years == {2025}
