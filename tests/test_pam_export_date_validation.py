"""PAM export: missing or malformed dates answer 400, not 500.

The export query interpolates its timestamp bounds into SQL so PostgreSQL can
use the `datetime_start` index. A missing date therefore used to reach the
database as "None 00:00:00" and come back as a server error — for every user,
admins included. These tests pin the plain answer instead.

Run:
    venv/Scripts/python -m pytest tests/test_pam_export_date_validation.py -v
"""
import pytest

from app.pam.utils import _require_export_date, get_occurrence_data


# ── the validator ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('value', [None, '', '   ', 'None', 'none'])
def test_a_missing_date_is_refused(value):
    with pytest.raises(ValueError) as excinfo:
        _require_export_date(value, 'start_date')
    assert 'start_date' in str(excinfo.value)


@pytest.mark.parametrize('value', ['2026-13-01', '01.09.2026', 'yesterday',
                                   '2026-09-31', "2026-09-01'; DROP TABLE"])
def test_a_malformed_date_is_refused(value):
    with pytest.raises(ValueError) as excinfo:
        _require_export_date(value, 'end_date')
    assert 'end_date' in str(excinfo.value)


@pytest.mark.parametrize('value,expected', [
    ('2026-09-01', '2026-09-01'),
    (' 2026-09-01 ', '2026-09-01'),
])
def test_a_good_date_is_normalised(value, expected):
    assert _require_export_date(value, 'start_date') == expected


def test_the_range_must_not_be_inverted(app):
    """Caught before any query runs, so no DB is touched."""
    with app.app_context():
        with pytest.raises(ValueError) as excinfo:
            get_occurrence_data({'start_date': '2026-09-10',
                                 'end_date': '2026-09-01'})
    assert 'later than' in str(excinfo.value)


def test_missing_dates_never_reach_the_database(app):
    with app.app_context():
        with pytest.raises(ValueError):
            get_occurrence_data({'confidence': 0.75})


# ── the endpoints ──────────────────────────────────────────────────────────

def test_preview_without_dates_answers_400(app, db_session, make_user):
    from flask import g
    from app.models import Institution, UserInstitution

    park = Institution(name_uk='НПП Тест', code='TST')
    db_session.add(park)
    db_session.commit()

    user = make_user(username='pam_exporter', roles=('analyst', 'pam_verifier'))
    user.institution_links.append(UserInstitution(
        institution_id=park.id, can_export=True,
        can_view_ct=True, can_export_ct=True,
        can_view_pam=True, can_export_pam=True))
    db_session.commit()

    g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True

    resp = client.get('/uk/api/pam/data-preview')
    assert resp.status_code == 400, 'a caller mistake, not a server failure'
    assert 'start_date' in (resp.get_json() or {}).get('error', '')


def test_download_without_dates_answers_400(app, db_session, make_user):
    from flask import g
    from app.models import Institution, UserInstitution

    park = Institution(name_uk='НПП Тест 2', code='TST2')
    db_session.add(park)
    db_session.commit()

    user = make_user(username='pam_exporter2', roles=('analyst', 'pam_verifier'))
    user.institution_links.append(UserInstitution(
        institution_id=park.id, can_export=True,
        can_view_pam=True, can_export_pam=True,
        can_view_ct=False, can_export_ct=False))
    db_session.commit()

    g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True

    resp = client.get('/uk/api/pam/data-download')
    assert resp.status_code == 400
    assert 'start_date' in resp.get_data(as_text=True)
