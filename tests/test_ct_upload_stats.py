"""
Tests for the admin "Upload statistics" page (camera_traps.upload_stats).

Covers:
  - query_upload_stats — sliding windows (today/week/month/year/total) over real
    in-memory ct_session data; metric = number of PHOTOS, attributed by upload
    time/uploader from the UploadBatch with observation-based backfill;
  - backfill: photos with NULL upload_batch_id fall back to the observation's
    created_at / uploaded_by_id;
  - query_upload_daily — per-day totals with gap-filling;
  - /upload-stats route — admin-only access; range switcher (30/90/365);
  - empty result -> info message.

Run:
    venv/Scripts/python -m pytest tests/test_ct_upload_stats.py -v
"""
import uuid
from collections import namedtuple
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _add_upload(ct_session, location, user_id, upload_time, n_photos=1,
                use_batch=True, captured_at=None, obs_user_id=None):
    """Create an Observation (+ optional UploadBatch) and `n_photos` photos.

    `upload_time` is the UPLOAD time. With use_batch=True it drives
    UploadBatch.created_at (the real source); the observation gets a deliberately
    different created_at so tests prove the batch is preferred. With
    use_batch=False the photos have NULL upload_batch_id and the observation's
    created_at / uploaded_by_id provide the backfill.

    `captured_at` is the camera capture time (defaults to an old fixed date, to
    confirm windows do NOT depend on capture time).
    """
    from app.camera_traps.models import Observation, Photo, UploadBatch

    if captured_at is None:
        captured_at = datetime(2021, 1, 1, 12, 0)

    # Observation gets a different created_at when a batch is present, to ensure
    # the window logic uses the batch time, not the observation time.
    obs_created = upload_time if not use_batch else upload_time + timedelta(days=900)
    obs = Observation(
        location_id=location.id,
        series_start_time=captured_at,
        series_end_time=captured_at + timedelta(minutes=5),
        uploaded_by_id=obs_user_id if obs_user_id is not None else user_id,
        created_at=obs_created,
    )
    ct_session.add(obs)
    ct_session.flush()

    batch_id = None
    if use_batch:
        batch = UploadBatch(
            id=str(uuid.uuid4()),
            location_id=location.id,
            uploaded_by_id=user_id,
            created_at=upload_time,
        )
        ct_session.add(batch)
        ct_session.flush()
        batch_id = batch.id

    for i in range(n_photos):
        ct_session.add(Photo(
            observation_id=obs.id,
            upload_batch_id=batch_id,
            original_filename=f'IMG_{obs.id}_{i}.jpg',
            system_filename=f'sys_{obs.id}_{i}_{user_id}_{uuid.uuid4().hex[:8]}.jpg',
            captured_at=captured_at,
        ))
    ct_session.commit()
    return obs


def _run_stats(ct_session, today):
    from app.camera_traps.routes import query_upload_stats
    return query_upload_stats(ct_session, today, text("1=1"), {})


# ──────────────────────────────────────────────────────────────────────────
# 1. query_upload_stats — sliding-window logic on real data
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def seeded_uploads(ct_session, make_ct_location):
    """User 1 uploads photos at various distances from `today` (by batch time),
    user 2 uploads today. Photo counts per upload vary to test PHOTO counting."""
    today = date(2025, 6, 15)
    loc = make_ct_location()

    def dt(days_ago):
        return datetime.combine(today, datetime.min.time()) - timedelta(days=days_ago) \
            + timedelta(hours=12)

    # user 1: 2 photos today, 3 within week, 1 within month, 1 within year, 1 older
    _add_upload(ct_session, loc, 1, dt(0), n_photos=2)
    _add_upload(ct_session, loc, 1, dt(3), n_photos=3)
    _add_upload(ct_session, loc, 1, dt(20), n_photos=1)
    _add_upload(ct_session, loc, 1, dt(100), n_photos=1)
    _add_upload(ct_session, loc, 1, dt(500), n_photos=1)
    # user 2: 4 photos today
    _add_upload(ct_session, loc, 2, dt(0), n_photos=4)
    return today, loc


def test_photo_window_counts_for_single_user(ct_session, seeded_uploads):
    today, _ = seeded_uploads
    rows = _run_stats(ct_session, today)
    r1 = {r.user_id: r for r in rows}[1]
    assert r1.d_today == 2           # 2 photos today
    assert r1.d_week == 5            # 2 + 3
    assert r1.d_month == 6           # 2 + 3 + 1
    assert r1.d_year == 7            # 2 + 3 + 1 + 1
    assert r1.total == 8             # all


def test_grouping_and_ordering_by_total_desc(ct_session, seeded_uploads):
    today, _ = seeded_uploads
    rows = _run_stats(ct_session, today)
    # user 1 total 8, user 2 total 4 → user 1 first
    assert [r.user_id for r in rows] == [1, 2]
    assert rows[0].total == 8
    assert rows[1].total == 4
    assert rows[1].d_today == 4


def test_windows_use_upload_time_not_capture_time(ct_session, make_ct_location):
    """Windows count by upload time (batch.created_at), NOT by captured_at."""
    today = date(2025, 6, 15)
    loc = make_ct_location()
    # Captured today, but uploaded a year ago → must not land in d_today/week/month.
    _add_upload(
        ct_session, loc, user_id=7,
        upload_time=datetime(2024, 1, 1, 12, 0),
        captured_at=datetime.combine(today, datetime.min.time()),
    )
    rows = _run_stats(ct_session, today)
    r = {x.user_id: x for x in rows}[7]
    assert r.d_today == 0
    assert r.d_week == 0
    assert r.d_month == 0
    assert r.total == 1


def test_null_batch_falls_back_to_observation(ct_session, make_ct_location):
    """Photos with NULL upload_batch_id are attributed via the observation
    (created_at + uploaded_by_id) — the oldest-seed backfill."""
    today = date(2025, 6, 15)
    loc = make_ct_location()
    up = datetime.combine(today, datetime.min.time()) + timedelta(hours=12)
    # No batch → observation.created_at == upload time, observation.uploaded_by_id == 9
    _add_upload(ct_session, loc, user_id=9, upload_time=up, n_photos=3, use_batch=False)

    rows = _run_stats(ct_session, today)
    r = {x.user_id: x for x in rows}[9]
    assert r.d_today == 3
    assert r.total == 3


def test_batch_time_preferred_over_observation_time(ct_session, make_ct_location):
    """When a batch exists, its created_at wins over the observation's created_at
    (which _add_upload sets ~900 days later)."""
    today = date(2025, 6, 15)
    loc = make_ct_location()
    up = datetime.combine(today, datetime.min.time()) + timedelta(hours=12)
    _add_upload(ct_session, loc, user_id=5, upload_time=up, n_photos=2, use_batch=True)
    rows = _run_stats(ct_session, today)
    r = {x.user_id: x for x in rows}[5]
    # If the observation time (today+900d) had been used, d_today would be 0.
    assert r.d_today == 2
    assert r.total == 2


def test_location_filter_via_inst_condition(ct_session, seeded_uploads, make_ct_location):
    """A location-narrowing institution condition excludes other locations."""
    from app.camera_traps.routes import query_upload_stats
    today, loc = seeded_uploads
    other = make_ct_location(name='Other', latitude=50.0, longitude=25.0)
    up = datetime.combine(today, datetime.min.time()) + timedelta(hours=12)
    _add_upload(ct_session, other, user_id=3, upload_time=up, n_photos=5)

    rows = query_upload_stats(
        ct_session, today,
        text("locations.id = :lid"), {'lid': loc.id},
    )
    assert 3 not in {r.user_id for r in rows}


# ──────────────────────────────────────────────────────────────────────────
# 2. query_upload_daily — per-day totals + gap-filling
# ──────────────────────────────────────────────────────────────────────────

def test_daily_gapfill_length_and_counts(ct_session, make_ct_location):
    from app.camera_traps.routes import query_upload_daily
    today = date(2025, 6, 15)
    loc = make_ct_location()

    def dt(days_ago):
        return datetime.combine(today, datetime.min.time()) - timedelta(days=days_ago) \
            + timedelta(hours=10)

    _add_upload(ct_session, loc, 1, dt(0), n_photos=2)
    _add_upload(ct_session, loc, 1, dt(2), n_photos=3)

    daily = query_upload_daily(ct_session, today, 30, text("1=1"), {})
    assert len(daily) == 30
    by_day = {d['day']: d['count'] for d in daily}
    assert by_day[today.strftime('%Y-%m-%d')] == 2
    assert by_day[(today - timedelta(days=2)).strftime('%Y-%m-%d')] == 3
    # A day with no uploads is present and zero (gap-filled).
    assert by_day[(today - timedelta(days=1)).strftime('%Y-%m-%d')] == 0
    # Last entry is today; first entry is 29 days ago.
    assert daily[-1]['day'] == today.strftime('%Y-%m-%d')
    assert daily[0]['day'] == (today - timedelta(days=29)).strftime('%Y-%m-%d')


def test_daily_window_excludes_older_than_range(ct_session, make_ct_location):
    from app.camera_traps.routes import query_upload_daily
    today = date(2025, 6, 15)
    loc = make_ct_location()
    # 40 days ago — outside a 30-day window.
    _add_upload(ct_session, loc, 1,
                datetime.combine(today, datetime.min.time()) - timedelta(days=40),
                n_photos=9)
    daily = query_upload_daily(ct_session, today, 30, text("1=1"), {})
    assert sum(d['count'] for d in daily) == 0


# ──────────────────────────────────────────────────────────────────────────
# 3. /upload-stats route — access, range switcher, rendering
# ──────────────────────────────────────────────────────────────────────────

Row = namedtuple('Row', 'user_id d_today d_week d_month d_year total')

URL = '/uk/camera-traps/upload-stats'


def _patch_ct(monkeypatch, rows, daily=None):
    mock_session = MagicMock()
    monkeypatch.setattr('app.camera_traps.routes.get_ct_session', lambda: mock_session)
    monkeypatch.setattr('app.camera_traps.routes.close_ct_session', lambda: None)
    monkeypatch.setattr('app.camera_traps.routes.query_upload_stats',
                        lambda *a, **k: rows)
    monkeypatch.setattr('app.camera_traps.routes.query_upload_daily',
                        lambda *a, **k: (daily if daily is not None else []))


def test_anonymous_redirected_to_login(client, monkeypatch):
    _patch_ct(monkeypatch, [])
    resp = client.get(URL)
    assert resp.status_code in (301, 302)


def test_regular_user_forbidden(auth_client, monkeypatch):
    _patch_ct(monkeypatch, [])
    cl = auth_client(role='viewer')
    resp = cl.get(URL)
    assert resp.status_code in (403, 302)


def test_admin_sees_page_and_full_name(auth_client, make_user, monkeypatch):
    u = make_user(username='uploader_u')
    u.first_name, u.last_name = 'Іван', 'Петренко'
    from app.extensions import db
    db.session.commit()

    _patch_ct(monkeypatch, [Row(u.id, 2, 5, 6, 7, 8)])
    cl = auth_client(role='admin')
    resp = cl.get(URL)
    assert resp.status_code == 200
    body = resp.data.decode('utf-8')
    assert 'Іван Петренко' in body
    assert 'id="uploads-table"' in body


def test_range_switcher_default_and_validation(auth_client, monkeypatch):
    captured = {}

    def fake_daily(ct_session, today, days, *a, **k):
        captured['days'] = days
        return []

    cl = auth_client(role='admin')
    _patch_ct(monkeypatch, [])
    monkeypatch.setattr('app.camera_traps.routes.query_upload_daily', fake_daily)

    # default
    cl.get(URL)
    assert captured['days'] == 30
    # valid
    cl.get(URL + '?days=90')
    assert captured['days'] == 90
    cl.get(URL + '?days=365')
    assert captured['days'] == 365
    # invalid -> 30
    cl.get(URL + '?days=7')
    assert captured['days'] == 30


def test_range_buttons_rendered(auth_client, monkeypatch):
    _patch_ct(monkeypatch, [])
    cl = auth_client(role='admin')
    resp = cl.get(URL)
    body = resp.data.decode('utf-8')
    assert 'days=30' in body
    assert 'days=90' in body
    assert 'days=365' in body


def test_empty_results_show_info_message(auth_client, monkeypatch):
    _patch_ct(monkeypatch, [])
    cl = auth_client(role='admin')
    resp = cl.get(URL)
    body = resp.data.decode('utf-8')
    assert 'id="uploads-table"' not in body
    assert 'немає даних про завантаження' in body


def test_scope_select_rendered(auth_client, monkeypatch):
    _patch_ct(monkeypatch, [])
    cl = auth_client(role='admin')
    resp = cl.get(URL)
    body = resp.data.decode('utf-8')
    assert 'id="scope-select"' in body
    assert 'name="scope"' in body
    assert 'value="global:"' in body
    # No species filter on this page.
    assert 'name="species_id"' not in body
