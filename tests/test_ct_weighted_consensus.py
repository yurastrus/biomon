# SPDX-License-Identifier: AGPL-3.0-only
"""Weighted closing rule (owner's decision 2026-11-23).

person's vote = confirmed / (n + 1) on that species; DeepFaune adds
0.5 x score to a species a person named; close when the leader is ahead
by >= 1.5. Finished series are never touched.
"""
import random
from types import SimpleNamespace

import pytest

from app.camera_traps import verifier_weights
from app.camera_traps.consensus import (
    UNIDENTIFIABLE_SPECIES_ID, grade_votes, pick_series_ai, verifier_weight,
    weighted_close)
from app.camera_traps.models import Identification, VerifierSpeciesWeight
from app.camera_traps.utils import check_consensus_for_observation

ROE, RED, EMPTY, KULAN, HORSE = 10, 11, -1, 20, 21


# --- pure rule ---------------------------------------------------------------

def test_weight_is_precision_times_experience():
    assert verifier_weight(0, 0) == 0
    assert verifier_weight(10, 10) == pytest.approx(10 / 11)
    assert verifier_weight(9, 10) == pytest.approx(0.9 * 10 / 11)


def test_grade_votes_against_the_other_people():
    graded = grade_votes([
        [(1, ROE), (2, ROE)],                 # both confirmed
        [(1, ROE), (2, RED), (3, RED)],       # 1 contradicted; 2 and 3: others tie -> not graded
        [(1, ROE), (2, RED)],                 # 1:1 -> each contradicted by the other
        [(5, ROE)],                           # alone: nothing to grade
    ])
    assert graded[(1, ROE)] == (3, 1)
    assert graded[(2, ROE)] == (1, 1)
    assert graded[(2, RED)] == (1, 0)
    assert (3, RED) not in graded
    assert all(u != 5 for u, _s in graded)


def test_grade_votes_counts_people_not_rows():
    one_row = grade_votes([[(1, ROE), (2, ROE)]])
    many_rows = grade_votes([[(1, ROE)] * 5 + [(2, ROE)] * 5])
    assert one_row == many_rows


def _w(table):
    return lambda u, s: table.get((u, s), 0.0)


def test_two_strong_people_close_one_person_plus_ai_does_not():
    w = _w({(1, EMPTY): 0.99, (2, EMPTY): 0.99})
    assert weighted_close([(1, EMPTY), (2, EMPTY)], w) == EMPTY
    assert weighted_close([(1, EMPTY)], w, ai=(EMPTY, 1.0)) is None   # 0.99 + 0.5 < 1.5


def test_weak_pair_waits_and_a_third_vote_closes():
    w = _w({(1, ROE): 0.7, (2, ROE): 0.7, (3, ROE): 0.9})
    assert weighted_close([(1, ROE), (2, ROE)], w) is None             # 1.4
    assert weighted_close([(1, ROE), (2, ROE)], w, ai=(ROE, 0.9)) == ROE   # 1.4 + 0.45
    assert weighted_close([(1, ROE), (2, ROE), (3, ROE)], w) == ROE


def test_ai_backs_people_but_never_pushes_against_them():
    w = _w({(1, KULAN): 0.96, (2, KULAN): 0.94})
    # DeepFaune has no kulan and says 'horse': it is ignored, not subtracted
    assert weighted_close([(1, KULAN), (2, KULAN)], w, ai=(HORSE, 0.99)) == KULAN
    # and its species alone never closes a series nobody named it in
    assert weighted_close([(1, KULAN)], w, ai=(HORSE, 1.0), threshold=0.0) == KULAN


def test_opposing_votes_are_subtracted():
    w = _w({(1, ROE): 0.95, (2, ROE): 0.95, (3, RED): 0.95})
    assert weighted_close([(1, ROE), (2, ROE), (3, RED)], w) is None   # 1.9 - 0.95
    w4 = _w({(1, ROE): 0.95, (2, ROE): 0.95, (3, RED): 0.95, (4, ROE): 0.95})
    assert weighted_close([(1, ROE), (2, ROE), (3, RED), (4, ROE)], w4) == ROE


def test_leader_needs_a_head_count_majority():
    # strong minority against many unknown people: points lead, heads do not
    w = _w({(1, ROE): 0.99, (2, ROE): 0.99})
    votes = [(1, ROE), (2, ROE), (3, RED), (4, RED), (5, RED)]
    assert weighted_close(votes, w, threshold=1.5) is None


def test_unidentifiable_still_needs_five_people():
    w = _w({(u, UNIDENTIFIABLE_SPECIES_ID): 0.99 for u in range(1, 6)})
    four = [(u, UNIDENTIFIABLE_SPECIES_ID) for u in range(1, 5)]
    assert weighted_close(four, w) is None
    assert weighted_close(four + [(5, UNIDENTIFIABLE_SPECIES_ID)], w) == UNIDENTIFIABLE_SPECIES_ID


def test_multi_species_keeps_the_every_species_decided_rule():
    w = _w({(1, ROE): 0.99, (2, ROE): 0.99, (1, RED): 0.99})
    # 1 named roe deer and red deer, 2 roe deer only: red deer at exactly half
    assert weighted_close([(1, ROE), (1, RED), (2, ROE)], w) is None


def test_pick_series_ai_matches_the_shared_python_pick():
    from app.camera_traps.ai_runner import pick_series_prediction
    rng = random.Random(7)
    for _ in range(300):
        preds = []
        for _f in range(rng.randint(1, 6)):
            for model_id, rank in ((1, 1), (2, rng.choice([0, 1, 2]))):
                preds.append((SimpleNamespace(
                    model_id=model_id,
                    prediction_species_id=rng.choice([EMPTY, ROE, RED, None]),
                    prediction_score=rng.choice([None, round(rng.random(), 2)])), rank))
        expected = pick_series_prediction(preds)
        agg = {}
        for p, rank in preds:
            key = (rank, p.model_id, p.prediction_species_id)
            frames, best = agg.get(key, (0, None))
            s = p.prediction_score
            agg[key] = (frames + 1, s if best is None else (best if s is None else max(best, s)))
        rows = [(r, m, sp, f, b) for (r, m, sp), (f, b) in agg.items()]
        got = pick_series_ai(rows)
        assert got[0] == expected.prediction_species_id
        assert got[1] == pytest.approx(expected.prediction_score or 0.0)


# --- wired into voting ---------------------------------------------------------

@pytest.fixture
def weights_table(ct_session):
    VerifierSpeciesWeight.__table__.create(bind=ct_session.get_bind(), checkfirst=True)
    verifier_weights.forget_availability()
    yield
    verifier_weights.forget_availability()


def _records(ct_session, rows):
    for u, s, n, c in rows:
        ct_session.add(VerifierSpeciesWeight(user_id=u, species_id=s, n=n, confirmed=c))
    ct_session.commit()
    verifier_weights.forget_availability()


def _vote(ct_session, photo, user_id, species_id):
    ct_session.add(Identification(photo_id=photo.id, user_id=user_id, species_id=species_id))
    ct_session.commit()


def _check(app, ct_session, obs, min_ids=2, **kw):
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'MIN_IDENTIFICATIONS': min_ids,
                                            'WEIGHTED_CONSENSUS_THRESHOLD': 1.49}
        check_consensus_for_observation(obs.id, db_session=ct_session, **kw)
        ct_session.commit()
    ct_session.refresh(obs)
    return obs.status


def test_strong_pair_closes_weak_pair_waits(app, ct_session, weights_table,
                                            make_ct_photo, make_ct_species):
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 100, 99), (2, sp.id, 100, 98),
                          (3, sp.id, 10, 7), (4, sp.id, 10, 7)])
    strong = make_ct_photo()
    _vote(ct_session, strong, 1, sp.id)
    _vote(ct_session, strong, 2, sp.id)
    assert _check(app, ct_session, strong.observation) == 'completed'

    weak = make_ct_photo()
    _vote(ct_session, weak, 3, sp.id)
    _vote(ct_session, weak, 4, sp.id)
    assert _check(app, ct_session, weak.observation) == 'pending'      # 0.64 + 0.64
    _vote(ct_session, weak, 1, sp.id)
    assert _check(app, ct_session, weak.observation) == 'completed'


def test_newcomers_alone_never_close(app, ct_session, weights_table,
                                     make_ct_photo, make_ct_species):
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 100, 99)])       # table not empty, 7 and 8 unknown
    photo = make_ct_photo()
    _vote(ct_session, photo, 7, sp.id)
    _vote(ct_session, photo, 8, sp.id)
    assert _check(app, ct_session, photo.observation) == 'pending'


def test_empty_table_falls_back_to_the_majority_rule(app, ct_session, weights_table,
                                                     make_ct_photo, make_ct_species):
    sp = make_ct_species()
    photo = make_ct_photo()
    _vote(ct_session, photo, 7, sp.id)
    _vote(ct_session, photo, 8, sp.id)
    assert _check(app, ct_session, photo.observation) == 'completed'


def test_finished_series_are_left_alone(app, ct_session, weights_table,
                                        make_ct_photo, make_ct_species):
    """A series closed under the old rule by two newcomers stays closed: a new
    vote does not recount it, and a moderator's confirming vote does not
    reopen it, although the weighted rule would not have closed it."""
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 100, 99)])
    photo = make_ct_photo(status='completed')
    obs = photo.observation
    obs.status = 'completed'
    ct_session.commit()
    _vote(ct_session, photo, 7, sp.id)
    _vote(ct_session, photo, 8, sp.id)
    assert _check(app, ct_session, obs) == 'completed'
    _vote(ct_session, photo, 9, sp.id)
    assert _check(app, ct_session, obs, moderator_override=True,
                  moderator_user_id=9) == 'completed'


def test_moderator_disagreeing_still_reopens(app, ct_session, weights_table,
                                             make_ct_photo, make_ct_species):
    sp = make_ct_species()
    other = make_ct_species(scientific_name='Capreolus capreolus')
    _records(ct_session, [(1, sp.id, 100, 99)])
    photo = make_ct_photo(status='completed')
    obs = photo.observation
    obs.status = 'completed'
    ct_session.commit()
    _vote(ct_session, photo, 7, sp.id)
    _vote(ct_session, photo, 8, sp.id)
    _vote(ct_session, photo, 9, other.id)
    assert _check(app, ct_session, obs, moderator_override=True,
                  moderator_user_id=9) == 'pending'


def test_refresh_rebuilds_records_from_votes(app, ct_session, weights_table,
                                             make_ct_photo, make_ct_species):
    sp = make_ct_species()
    for _ in range(3):
        photo = make_ct_photo()
        _vote(ct_session, photo, 1, sp.id)
        _vote(ct_session, photo, 2, sp.id)
    with app.app_context():
        rows = verifier_weights.refresh(ct_session.get_bind())
    assert rows == 2
    got = {(r.user_id, r.species_id): (r.n, r.confirmed)
           for r in ct_session.query(VerifierSpeciesWeight).all()}
    assert got == {(1, sp.id): (3, 3), (2, sp.id): (3, 3)}


def test_min_one_lets_a_reliable_person_close_only_with_weights(app, ct_session, weights_table,
                                                                make_ct_photo, make_ct_species,
                                                                monkeypatch):
    """MIN_IDENTIFICATIONS 1 + threshold 1.49: one person with weight >= 0.99
    plus DeepFaune (score 1.0) closes; a weaker person does not."""
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 200, 199), (2, sp.id, 20, 19)])
    monkeypatch.setattr(verifier_weights, 'series_ai_answers',
                        lambda session, ids: {i: (sp.id, 1.0) for i in ids})
    strong = make_ct_photo()
    _vote(ct_session, strong, 1, sp.id)                 # 199/201 = 0.990 + 0.5
    assert _check(app, ct_session, strong.observation, min_ids=1) == 'completed'
    weak = make_ct_photo()
    _vote(ct_session, weak, 2, sp.id)                   # 19/21 = 0.905 + 0.5
    assert _check(app, ct_session, weak.observation, min_ids=1) == 'pending'


def test_min_one_never_lets_the_majority_fallback_close_on_one_vote(app, ct_session,
                                                                    make_ct_photo,
                                                                    make_ct_species):
    # no weights table at all -> majority rule; one vote must not close
    verifier_weights.forget_availability()
    sp = make_ct_species()
    photo = make_ct_photo()
    _vote(ct_session, photo, 1, sp.id)
    assert _check(app, ct_session, photo.observation, min_ids=1) == 'pending'
    _vote(ct_session, photo, 2, sp.id)
    assert _check(app, ct_session, photo.observation, min_ids=1) == 'completed'


# --- admin page -----------------------------------------------------------------

@pytest.fixture
def weights_route_session(ct_session):
    from unittest.mock import patch
    with patch('app.camera_traps.weights_routes.get_ct_session', return_value=ct_session), \
         patch('app.camera_traps.weights_routes.close_ct_session'):
        yield ct_session


def test_weights_page_admin_only(auth_client, db_session, weights_route_session):
    resp = auth_client(role='manager').get('/uk/camera-traps/admin/verifier-weights')
    assert resp.status_code in (302, 403)


def test_weights_page_without_table_says_so(auth_client, db_session, weights_route_session):
    resp = auth_client(role='admin').get('/uk/camera-traps/admin/verifier-weights')
    assert resp.status_code == 200
    assert 'правилом більшості' in resp.get_data(as_text=True)


def test_weights_page_lists_people_and_weights(auth_client, db_session, weights_route_session,
                                               weights_table, make_ct_species):
    sp = make_ct_species(common_name_ua='Козуля')
    _records(weights_route_session, [(1, sp.id, 200, 199), (2, sp.id, 10, 7)])
    cl = auth_client(role='admin')
    resp = cl.get('/uk/camera-traps/admin/verifier-weights')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'Козуля' in html
    assert '0.990' in html          # 199 / 201
    assert '0.636' in html          # 7 / 11
    assert html.count('<table class="vw-table" data-sortable') == 2   # both tables sort
    assert 'data-v="0.9900"' in html                 # numeric sort key on the weight
    one = cl.get(
        f'/uk/camera-traps/admin/verifier-weights?user=2&species={sp.id}')
    assert '0.636' in one.get_data(as_text=True)
    assert '0.990' not in one.get_data(as_text=True).split('id="detail"')[1]


def test_weights_refresh_button_starts_background_job(auth_client, db_session,
                                                      weights_route_session, weights_table,
                                                      monkeypatch):
    started = []
    monkeypatch.setattr(verifier_weights, 'start_refresh', lambda s: started.append(1) or True)
    resp = auth_client(role='admin').post('/uk/camera-traps/admin/verifier-weights/refresh')
    assert resp.status_code in (302, 303)
    assert started == [1]


def test_admin_panel_links_to_weights_page():
    import pathlib
    html = pathlib.Path('app/camera_traps/templates/admin.html').read_text(encoding='utf-8')
    assert "camera_traps.verifier_weights_page" in html


def test_experience_k_keeps_newcomers_newcomers():
    assert verifier_weight(10, 10, k=10) == pytest.approx(0.5)
    assert verifier_weight(990, 990, k=10) == pytest.approx(0.99)
    assert verifier_weight(10, 10) == pytest.approx(10 / 11)          # default k = 1


def test_configured_k_stops_two_newcomers_closing(app, ct_session, weights_table,
                                                  make_ct_photo, make_ct_species):
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 10, 10), (2, sp.id, 10, 10)])
    photo = make_ct_photo()
    _vote(ct_session, photo, 1, sp.id)
    _vote(ct_session, photo, 2, sp.id)
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = {'MIN_IDENTIFICATIONS': 1,
                                            'WEIGHTED_CONSENSUS_THRESHOLD': 1.49,
                                            'VERIFIER_EXPERIENCE_K': 10}
        check_consensus_for_observation(photo.observation.id, db_session=ct_session)
        ct_session.commit()
    ct_session.refresh(photo.observation)
    assert photo.observation.status == 'pending'                   # 0.5 + 0.5 < 1.49
    assert _check(app, ct_session, photo.observation) == 'completed'   # k = 1: 0.91 + 0.91


# --- threshold kept in ct_db (ct_settings), config as fallback -------------------

from app.camera_traps import ct_settings  # noqa: E402
from app.camera_traps.models import CTSetting  # noqa: E402


@pytest.fixture
def settings_table(ct_session):
    CTSetting.__table__.create(bind=ct_session.get_bind(), checkfirst=True)
    ct_settings.forget()
    yield
    ct_settings.forget()


def test_threshold_falls_back_to_config_and_reads_the_stored_value(app, ct_session,
                                                                   settings_table):
    with app.app_context():
        cfg = {'WEIGHTED_CONSENSUS_THRESHOLD': 1.5}
        assert ct_settings.weighted_threshold(ct_session, cfg) == 1.5
        ct_settings.put(ct_session, ct_settings.THRESHOLD_KEY, 1.49, 'admin')
        ct_session.commit()
        assert ct_settings.weighted_threshold(ct_session, cfg) == 1.49
        ct_settings.put(ct_session, ct_settings.THRESHOLD_KEY, 9.0, 'admin')   # out of range
        ct_session.commit()
        assert ct_settings.weighted_threshold(ct_session, cfg) == 1.5


def test_threshold_without_table_uses_config(app, ct_session):
    ct_settings.forget()
    with app.app_context():
        assert ct_settings.weighted_threshold(ct_session, {'WEIGHTED_CONSENSUS_THRESHOLD': 1.5}) == 1.5


@pytest.mark.parametrize('raw, expected', [('1.49', 1.49), ('1,4', 1.4), ('0.9', None),
                                           ('3.5', None), ('abc', None), (None, None)])
def test_parse_threshold(raw, expected):
    assert ct_settings.parse_threshold(raw) == expected


def test_stored_threshold_drives_closing(app, ct_session, weights_table, settings_table,
                                         make_ct_photo, make_ct_species):
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 100, 95), (2, sp.id, 100, 95)])   # 0.94 each
    photo = make_ct_photo()
    _vote(ct_session, photo, 1, sp.id)
    _vote(ct_session, photo, 2, sp.id)
    with app.app_context():
        ct_settings.put(ct_session, ct_settings.THRESHOLD_KEY, 2.0, 'admin')
        ct_session.commit()
        app.config['CAMERA_TRAP_CONFIG'] = {'MIN_IDENTIFICATIONS': 1,
                                            'WEIGHTED_CONSENSUS_THRESHOLD': 1.5}
        check_consensus_for_observation(photo.observation.id, db_session=ct_session)
        ct_session.commit()
    ct_session.refresh(photo.observation)
    assert photo.observation.status == 'pending'          # 1.88 < 2.0 stored, config 1.5 ignored


@pytest.fixture
def settings_route_session(weights_route_session, settings_table):
    yield weights_route_session


def test_admin_saves_threshold(auth_client, db_session, settings_route_session):
    cl = auth_client(role='admin')
    resp = cl.post('/uk/camera-traps/admin/verifier-weights/settings', data={'threshold': '1.47'})
    assert resp.status_code in (302, 303)
    stored = ct_settings.info(settings_route_session, ct_settings.THRESHOLD_KEY)
    assert stored[0] == '1.47' and stored[2] == 'test_admin'
    bad = cl.post('/uk/camera-traps/admin/verifier-weights/settings', data={'threshold': '0.5'})
    assert bad.status_code in (302, 303)
    assert ct_settings.info(settings_route_session, ct_settings.THRESHOLD_KEY)[0] == '1.47'


def test_manager_cannot_save_threshold(auth_client, db_session, settings_route_session):
    resp = auth_client(role='manager').post('/uk/camera-traps/admin/verifier-weights/settings',
                                            data={'threshold': '1.2'})
    assert resp.status_code in (302, 403)
    assert ct_settings.info(settings_route_session, ct_settings.THRESHOLD_KEY) is None


def test_estimate_lists_who_gains_alone_plus_ai(auth_client, db_session, settings_route_session,
                                                weights_table, make_ct_species, make_ct_photo):
    sp = make_ct_species(common_name_ua='Козуля')
    _records(settings_route_session, [(1, sp.id, 200, 190)])            # 190/210 = 0.905
    photo = make_ct_photo()
    _vote(settings_route_session, photo, 1, sp.id)
    cl = auth_client(role='admin')
    with app_config(cl, {'MIN_IDENTIFICATIONS': 1, 'WEIGHTED_CONSENSUS_THRESHOLD': 1.49,
                         'VERIFIER_EXPERIENCE_K': 10, 'AI_VOTE_FACTOR': 0.5}):
        d = cl.get('/uk/camera-traps/admin/verifier-weights/estimate?threshold=1.40').get_json()
    assert d['old'] == 1.49 and d['new'] == 1.4
    assert d['solo_new'] == pytest.approx(0.9)
    assert [g['species'] for g in d['gained']] == ['Козуля']
    assert d['lost'] == []
    assert d['pending_total'] == 1
    assert isinstance(d['close_now_new'], int)
    assert isinstance(d['close_now_species'], list)
    bad = cl.get('/uk/camera-traps/admin/verifier-weights/estimate?threshold=7')
    assert bad.status_code == 400


class app_config:
    """Temporarily set CAMERA_TRAP_CONFIG on the client's app."""
    def __init__(self, client, cfg):
        self.app, self.cfg = client.application, cfg

    def __enter__(self):
        self.old = self.app.config.get('CAMERA_TRAP_CONFIG')
        self.app.config['CAMERA_TRAP_CONFIG'] = self.cfg

    def __exit__(self, *exc):
        self.app.config['CAMERA_TRAP_CONFIG'] = self.old


def test_stored_ai_factor_drives_closing(app, ct_session, weights_table, settings_table,
                                         make_ct_photo, make_ct_species, monkeypatch):
    """One person 0.99 + DeepFaune score 1.0: closes with factor 0.5 (1.49),
    stays open once the stored factor is 0.3 (1.29)."""
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 2000, 1999)])                       # 1999/2010 = 0.9945
    monkeypatch.setattr(verifier_weights, 'series_ai_answers',
                        lambda session, ids: {i: (sp.id, 1.0) for i in ids})
    cfg = {'MIN_IDENTIFICATIONS': 1, 'WEIGHTED_CONSENSUS_THRESHOLD': 1.49,
           'VERIFIER_EXPERIENCE_K': 10, 'AI_VOTE_FACTOR': 0.5}
    weak_ai = make_ct_photo()
    _vote(ct_session, weak_ai, 1, sp.id)
    with app.app_context():
        ct_settings.put(ct_session, ct_settings.AI_FACTOR_KEY, 0.3, 'admin')
        ct_session.commit()
        app.config['CAMERA_TRAP_CONFIG'] = cfg
        check_consensus_for_observation(weak_ai.observation.id, db_session=ct_session)
        ct_session.commit()
        ct_session.refresh(weak_ai.observation)
        assert weak_ai.observation.status == 'pending'
        ct_settings.put(ct_session, ct_settings.AI_FACTOR_KEY, 0.5, 'admin')
        ct_session.commit()
        check_consensus_for_observation(weak_ai.observation.id, db_session=ct_session)
        ct_session.commit()
    ct_session.refresh(weak_ai.observation)
    assert weak_ai.observation.status == 'completed'


def test_admin_saves_ai_factor_and_rejects_out_of_range(auth_client, db_session,
                                                        settings_route_session):
    cl = auth_client(role='admin')
    cl.post('/uk/camera-traps/admin/verifier-weights/settings',
            data={'threshold': '1.5', 'ai_factor': '0.4'})
    assert ct_settings.info(settings_route_session, ct_settings.AI_FACTOR_KEY)[0] == '0.4'
    cl.post('/uk/camera-traps/admin/verifier-weights/settings',
            data={'threshold': '1.5', 'ai_factor': '1.5'})
    assert ct_settings.info(settings_route_session, ct_settings.AI_FACTOR_KEY)[0] == '0.4'


def test_estimate_takes_the_ai_factor(auth_client, db_session, settings_route_session,
                                      weights_table, make_ct_species):
    sp = make_ct_species(common_name_ua='Козуля')
    _records(settings_route_session, [(1, sp.id, 200, 190)])            # 0.905
    cl = auth_client(role='admin')
    with app_config(cl, {'MIN_IDENTIFICATIONS': 1, 'WEIGHTED_CONSENSUS_THRESHOLD': 1.49,
                         'VERIFIER_EXPERIENCE_K': 10, 'AI_VOTE_FACTOR': 0.5}):
        d = cl.get('/uk/camera-traps/admin/verifier-weights/estimate'
                   '?threshold=1.49&ai_factor=0.6').get_json()
    assert d['new_ai'] == 0.6 and d['old_ai'] == 0.5
    assert d['solo_new'] == pytest.approx(0.89)
    assert [g['species'] for g in d['gained']] == ['Козуля']


def test_stored_k_drives_weights_and_closing(app, ct_session, weights_table, settings_table,
                                             make_ct_photo, make_ct_species):
    """Two 20/20 people: k = 10 gives 0.67 each (1.33, open), stored k = 1 gives
    0.95 each (1.90, closed)."""
    sp = make_ct_species()
    _records(ct_session, [(1, sp.id, 20, 20), (2, sp.id, 20, 20)])
    photo = make_ct_photo()
    _vote(ct_session, photo, 1, sp.id)
    _vote(ct_session, photo, 2, sp.id)
    cfg = {'MIN_IDENTIFICATIONS': 1, 'WEIGHTED_CONSENSUS_THRESHOLD': 1.5,
           'VERIFIER_EXPERIENCE_K': 10, 'AI_VOTE_FACTOR': 0.5}
    with app.app_context():
        app.config['CAMERA_TRAP_CONFIG'] = cfg
        check_consensus_for_observation(photo.observation.id, db_session=ct_session)
        ct_session.commit()
        ct_session.refresh(photo.observation)
        assert photo.observation.status == 'pending'
        ct_settings.put(ct_session, ct_settings.EXPERIENCE_K_KEY, 1, 'admin')
        ct_session.commit()
        check_consensus_for_observation(photo.observation.id, db_session=ct_session)
        ct_session.commit()
    ct_session.refresh(photo.observation)
    assert photo.observation.status == 'completed'


def test_admin_saves_k_and_estimate_uses_it(auth_client, db_session, settings_route_session,
                                            weights_table, make_ct_species):
    sp = make_ct_species(common_name_ua='Козуля')
    _records(settings_route_session, [(1, sp.id, 200, 200)])            # k 10: 0.952, k 1: 0.995
    cl = auth_client(role='admin')
    with app_config(cl, {'MIN_IDENTIFICATIONS': 1, 'WEIGHTED_CONSENSUS_THRESHOLD': 1.49,
                         'VERIFIER_EXPERIENCE_K': 10, 'AI_VOTE_FACTOR': 0.5}):
        d = cl.get('/uk/camera-traps/admin/verifier-weights/estimate?k=1').get_json()
        assert d['old_k'] == 10 and d['new_k'] == 1
        assert [g['species'] for g in d['gained']] == ['Козуля']
        cl.post('/uk/camera-traps/admin/verifier-weights/settings',
                data={'threshold': '1.49', 'ai_factor': '0.5', 'k': '3'})
        assert ct_settings.info(settings_route_session, ct_settings.EXPERIENCE_K_KEY)[0] == '3.0'
        cl.post('/uk/camera-traps/admin/verifier-weights/settings',
                data={'threshold': '1.49', 'ai_factor': '0.5', 'k': '500'})
        assert ct_settings.info(settings_route_session, ct_settings.EXPERIENCE_K_KEY)[0] == '3.0'
        html = cl.get('/uk/camera-traps/admin/verifier-weights').get_data(as_text=True)
        assert '(n + 3)' in html
