"""
A moderator's vote on an already closed series (review mode) reopens it only
when the moderator disagrees with the accepted result.

Regression for 2026-11-19: series 126127 had three matching 'Empty' votes and
stayed pending, because any moderator vote on a completed series reopened it
without a recount.
"""
import pytest

from app.camera_traps.models import Identification
from app.camera_traps.utils import check_consensus_for_observation, mark_observation_complete

MODERATOR = 99


@pytest.fixture(autouse=True)
def _ctx(app):
    with app.app_context():
        yield


@pytest.fixture
def closed_series(app, ct_session, make_ct_photo, make_ct_species):
    """A series closed by two matching votes, plus a second species."""
    photo = make_ct_photo()
    obs = photo.observation
    agreed = make_ct_species()
    other = make_ct_species(scientific_name='Capreolus capreolus',
                            common_name_ua='Козуля', common_name_en='Roe deer')
    for uid in (1, 2):
        ct_session.add(Identification(photo_id=photo.id, user_id=uid,
                                      species_id=agreed.id))
    ct_session.flush()
    app.config['CAMERA_TRAP_CONFIG'] = dict(
        app.config.get('CAMERA_TRAP_CONFIG') or {}, MIN_IDENTIFICATIONS=2)
    mark_observation_complete(obs.id, db_session=ct_session,
                              winner_species_id=agreed.id)
    ct_session.commit()
    return obs, photo, agreed, other


def _moderator_votes(ct_session, obs, photo, species_ids):
    for sp in species_ids:
        ct_session.add(Identification(photo_id=photo.id, user_id=MODERATOR,
                                      species_id=sp))
    ct_session.flush()
    check_consensus_for_observation(obs.id, db_session=ct_session,
                                    moderator_override=True,
                                    moderator_user_id=MODERATOR)
    ct_session.commit()
    ct_session.refresh(obs)
    ct_session.refresh(photo)


def test_agreeing_moderator_keeps_series_closed(app, ct_session, closed_series):
    obs, photo, agreed, _other = closed_series
    _moderator_votes(ct_session, obs, photo, [agreed.id])
    assert obs.status == 'completed'
    assert photo.status == 'completed'


def test_disagreeing_moderator_reopens_series(app, ct_session, closed_series):
    obs, photo, _agreed, other = closed_series
    _moderator_votes(ct_session, obs, photo, [other.id])
    assert obs.status == 'pending'
    assert photo.status == 'pending'


def test_moderator_adding_an_unaccepted_species_reopens(app, ct_session, closed_series):
    """Agreeing on the main species but also naming a second one that only the
    moderator saw: the second species is not accepted, so the series reopens
    for another verifier to settle it."""
    obs, photo, agreed, other = closed_series
    _moderator_votes(ct_session, obs, photo, [agreed.id, other.id])
    assert obs.status == 'pending'


def test_override_without_moderator_id_still_reopens(app, ct_session, closed_series):
    """Callers that pass no moderator id keep the old behaviour."""
    obs, photo, agreed, _other = closed_series
    ct_session.add(Identification(photo_id=photo.id, user_id=MODERATOR,
                                  species_id=agreed.id))
    ct_session.flush()
    check_consensus_for_observation(obs.id, db_session=ct_session,
                                    moderator_override=True)
    ct_session.commit()
    ct_session.refresh(obs)
    assert obs.status == 'pending'
