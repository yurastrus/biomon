"""Pure logic of the weighted-consensus study (scripts/weighted_consensus_model.py)."""
import itertools
import math

import numpy as np
import pytest

from app.camera_traps.consensus import can_close, tally_votes
from scripts.weighted_consensus_model import (
    Model, Penalty, Stats, UNIDENTIFIABLE, first_close, loo_records, majority,
    next_vote_logloss, rule_current, rule_posterior, rule_sum, wilson_lower)

ROE, RED, EMPTY = 10, 11, -1


def _training():
    """Experts 1-2 always right; user 3 often wrong; plenty of roe deer."""
    series = []
    for _ in range(200):
        series.append([(1, ROE), (2, ROE)])
    for _ in range(40):
        series.append([(1, RED), (2, RED)])
    for _ in range(30):
        series.append([(1, ROE), (3, RED), (2, ROE)])
    for _ in range(30):
        series.append([(1, ROE), (3, ROE)])
    for _ in range(20):
        series.append([(2, EMPTY), (1, EMPTY)])
    return series


def test_majority_needs_unique_top():
    assert majority([(1, ROE), (2, ROE), (3, RED)]) == ROE
    assert majority([(1, ROE), (2, RED)]) is None
    assert majority([]) is None


def test_loo_grades_against_the_others_only():
    recs = loo_records([(1, ROE), (2, ROE), (3, RED)])
    assert (3, ROE, RED) in recs
    assert (1, None, ROE) not in recs
    # user 1's others are ROE vs RED: a tie, so user 1 is not graded
    assert all(u != 1 for u, _t, _s in recs)


def test_wilson_lower_penalises_small_samples():
    assert wilson_lower(10, 10) < 0.75
    assert wilson_lower(1000, 1000) > 0.99
    assert wilson_lower(0, 0) is None


@pytest.mark.parametrize('kind', ['wilson', 'beta_class', 'beta_skill'])
def test_ten_of_ten_is_trusted_less_than_raw(kind):
    st = Stats(_training())
    st.N[99][ROE] = 10
    st.H[99][ROE] = 10
    classes = [EMPTY, ROE, RED]
    raw = Model(st, classes, Penalty('raw', r_max=0.999)).row(99)[1]
    pen = Model(st, classes, Penalty(kind, m=30, r_max=0.999)).row(99)[1]
    assert raw == pytest.approx(0.999)
    assert pen < raw


def test_expert_outweighs_error_prone_user():
    m = Model(Stats(_training()), [EMPTY, ROE, RED], Penalty('beta_skill', m=3))
    assert m.row(1)[1] > m.row(3)[1]
    post = np.exp(m.log_posterior([(1, ROE), (3, RED)]))
    assert post[1] > post[2]


def test_posterior_rule_never_closes_on_one_vote_or_unnamed_species():
    m = Model(Stats(_training()), [EMPTY, ROE, RED], Penalty('beta_skill', m=3))
    assert rule_posterior(m, [(1, ROE)], tau=0.5) is None
    assert rule_posterior(m, [(1, ROE), (2, ROE)], tau=0.9) == ROE


def test_posterior_rule_needs_two_supporters():
    m = Model(Stats(_training()), [EMPTY, ROE, RED], Penalty('beta_skill', m=3))
    assert rule_posterior(m, [(1, ROE), (3, RED)], tau=0.01) is None
    assert rule_posterior(m, [(1, ROE), (3, RED)], tau=0.01, min_support=1) == ROE


def test_unidentifiable_needs_five_in_every_rule():
    m = Model(Stats(_training()), [UNIDENTIFIABLE, ROE], Penalty('raw'))
    votes = [(u, UNIDENTIFIABLE) for u in range(1, 5)]
    assert rule_current(votes) is None
    assert rule_sum(m, votes, threshold=0.1) is None
    assert rule_posterior(m, votes, tau=0.1) is None
    assert rule_current(votes + [(5, UNIDENTIFIABLE)]) == UNIDENTIFIABLE


def test_current_rule_matches_consensus_can_close():
    for votes in itertools.product([ROE, RED, EMPTY], repeat=3):
        for k in (2, 3):
            prefix = [(i, s) for i, s in enumerate(votes[:k])]
            per, voters = tally_votes((u, s, 1) for u, s in prefix)
            closes = (rule_current(prefix) is not None)
            assert closes == can_close(per, voters, multi=False)


def test_first_close_and_logloss_run():
    m = Model(Stats(_training()), [EMPTY, ROE, RED], Penalty('beta_skill', m=3))
    assert first_close(rule_current, [(1, ROE), (3, RED), (2, ROE)]) == (3, ROE)
    ll, n, calib = next_vote_logloss(m, [[(1, ROE), (2, ROE)]])
    assert n == 1 and ll > 0 and math.isfinite(ll) and len(calib) == 1


# --- the owner's agreed rule: weighted margin with DeepFaune ---------------

from scripts.weighted_consensus_model import (  # noqa: E402
    AgreedWeights, experience_factor, rule_margin)


def _agreed_training():
    shown = (True, True, True)
    series = []
    for _ in range(50):                              # 1 and 2 agree on roe deer
        series.append(([(1, ROE), (2, ROE), (4, ROE)], (ROE, 0.99, shown)))
    for _ in range(10):                              # AI wrong, user 3 copies it
        series.append(([(1, RED), (2, RED), (3, ROE)], (ROE, 0.99, shown)))
    for _ in range(10):                              # AI wrong, nobody copies
        series.append(([(1, RED), (2, RED), (4, RED)], (ROE, 0.99, shown)))
    return series


def test_experience_factor():
    assert experience_factor(0) == 0
    assert experience_factor(1) == 0.5
    assert experience_factor(10) == pytest.approx(10 / 11)


def test_human_weight_is_precision_times_experience():
    w = AgreedWeights(_agreed_training())
    assert w.human(99, ROE) == 0                     # never graded on roe deer
    n = w.said[1][ROE]
    assert w.human(1, ROE) == pytest.approx(w.hit[1][ROE] / n * n / (n + 1))
    assert w.human(3, ROE) < 0.5                     # user 3 mostly wrong on roe


def test_copying_is_detected_and_lowers_the_ai_weight():
    w = AgreedWeights(_agreed_training())
    assert w.copy_raw[ROE][0] > 0                    # user 3 repeated the AI
    assert 0 < w.copy[ROE] <= 1
    acc = w.ai_hit[(ROE, 4)] / w.ai_n[(ROE, 4)]
    assert w.ai(ROE, 0.995) < acc


def test_margin_subtracts_opposing_votes_and_ai_never_closes_alone():
    w = AgreedWeights(_agreed_training())
    ai = (ROE, 0.995, (True,))
    assert rule_margin(w, [(1, ROE), (2, ROE)], threshold=1.5) == ROE
    # defaults are the agreed rule: score x 0.5, AI only backs a named species
    assert rule_margin(w, [(1, RED), (2, RED)], (ROE, 1.0, (True, True)),
                       threshold=w.human(1, RED) + w.human(2, RED) - 0.1) == RED
    assert rule_margin(w, [(1, ROE), (2, RED)], threshold=0.5) is None
    assert rule_margin(w, [(1, RED)], ai=ai, threshold=0.0, ai_mode='copy',
                       ai_named_only=False) == RED
    # the AI's species with no person behind it never closes
    assert rule_margin(w, [(3, RED)], ai=(ROE, 0.995, (True,)), threshold=-5,
                       ai_mode='copy', ai_named_only=False) is None


def test_margin_keeps_todays_rule_for_unidentifiable():
    series = [([(u, UNIDENTIFIABLE) for u in range(1, 6)], None) for _ in range(30)]
    w = AgreedWeights(series)
    four = [(u, UNIDENTIFIABLE) for u in range(1, 5)]
    assert rule_margin(w, four, threshold=0.1) is None
    assert rule_margin(w, four + [(5, UNIDENTIFIABLE)], threshold=0.1) == UNIDENTIFIABLE


def test_ai_named_only_never_pushes_against_people():
    w = AgreedWeights(_agreed_training())
    votes = [(1, RED), (2, RED)]
    against = (ROE, 1.0, (True, True))
    lead_with = rule_margin(w, votes, against, threshold=0.0, ai_mode='score_half',
                            ai_named_only=False)
    assert lead_with == RED
    # with a threshold just under two people's points, the AI's 'roe deer'
    # blocks the series unless it may only back a species a person named
    two = w.human(1, RED) + w.human(2, RED)
    t = two - 0.1
    assert rule_margin(w, votes, against, threshold=t, ai_mode='score_half',
                       ai_named_only=False) is None
    assert rule_margin(w, votes, against, threshold=t, ai_mode='score_half',
                       ai_named_only=True) == RED
