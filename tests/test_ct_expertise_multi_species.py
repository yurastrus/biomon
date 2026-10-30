"""Expertise statistics once a verifier may name several species (2026-10).

The arithmetic was generalised from "one vote per verifier" to sets. The
contract that matters most: on data where every verifier names one species
(all production data before the change) the numbers are EXACTLY the old ones.
`_old_compute` below is the pre-change loop, frozen as the reference.
"""
import random
from collections import namedtuple
from datetime import datetime, timedelta

import pytest

from app.camera_traps.routes import compute_expertise_stats, final_species, loo_species

Vote = namedtuple('Vote', 'obs_id user_id species_id quantity voted_at observed_at')
DAY = datetime(2026, 3, 1, 12, 0)
SHOT = datetime(2026, 2, 1, 3, 0)
FOX, BADGER, ROE = 12, 13, 11
EMPTY = -1


def _old_compute(vote_rows, start_dt=None, end_dt=None, mode='users', species_id=None,
                 user_id=None, include_special=False, obs_start_dt=None, obs_end_dt=None):
    """compute_expertise_stats as it was before multi-species (reference)."""
    by_obs, observed_at = {}, {}
    for row in vote_rows:
        if row.species_id is None:
            continue
        by_obs.setdefault(row.obs_id, []).append(
            (row.user_id, row.species_id, row.quantity or 0, row.voted_at))
        observed_at.setdefault(row.obs_id, row.observed_at)
    stats, undecidable, total_series = {}, 0, 0
    by_species = (mode == 'species')

    def _slot(key):
        return stats.setdefault(key, {'n': 0, 'hits': 0, 'loo_n': 0, 'loo_hits': 0,
                                      'said': 0, 'said_hits': 0})
    for obs_id, rows in by_obs.items():
        shot_at = observed_at.get(obs_id)
        if shot_at is not None:
            if obs_start_dt is not None and shot_at < obs_start_dt:
                continue
            if obs_end_dt is not None and shot_at > obs_end_dt:
                continue
        votes = [(uid, sp, q) for uid, sp, q, _at in rows]
        final = final_species(votes)
        if final is None:
            continue
        if not include_special and final < 0:
            continue
        total_series += 1
        for uid, sp, _q, voted_at in rows:
            if user_id is not None and uid != user_id:
                continue
            if voted_at is not None:
                if start_dt is not None and voted_at < start_dt:
                    continue
                if end_dt is not None and voted_at > end_dt:
                    continue
            if by_species or species_id is None or final == species_id:
                slot = _slot(final if by_species else uid)
                slot['n'] += 1
                slot['hits'] += 1 if sp == final else 0
                loo = loo_species(votes, uid)
                if loo is None:
                    undecidable += 1
                else:
                    slot['loo_n'] += 1
                    slot['loo_hits'] += 1 if sp == loo else 0
            if not include_special and sp < 0:
                continue
            if by_species or (species_id is not None and sp == species_id):
                slot = _slot(sp if by_species else uid)
                slot['said'] += 1
                slot['said_hits'] += 1 if sp == final else 0
    return stats, {'series': total_series, 'undecidable': undecidable}


def _random_single_species(seed, n_series=600):
    rng = random.Random(seed)
    rows = []
    for obs in range(n_series):
        shot = SHOT + timedelta(days=rng.randint(0, 60))
        for user in rng.sample(range(1, 9), rng.randint(1, 5)):
            rows.append(Vote(obs, user, rng.choice([EMPTY, 10, 11, 12, 13, 14]),
                             rng.randint(1, 4), DAY + timedelta(days=rng.randint(0, 30)), shot))
    return rows


@pytest.mark.parametrize('seed', [1, 2, 3])
@pytest.mark.parametrize('kwargs', [
    {},
    {'mode': 'species'},
    {'mode': 'species', 'include_special': True},
    {'species_id': 12},
    {'mode': 'species', 'user_id': 3},
    {'start_dt': DAY + timedelta(days=10), 'end_dt': DAY + timedelta(days=20)},
    {'obs_start_dt': SHOT + timedelta(days=20), 'obs_end_dt': SHOT + timedelta(days=40)},
])
def test_single_species_data_gives_exactly_the_old_numbers(seed, kwargs):
    rows = _random_single_species(seed)
    assert compute_expertise_stats(rows, **kwargs) == _old_compute(rows, **kwargs)


def test_verifier_who_also_saw_the_badger_is_not_penalised():
    # Three people: all saw the fox, two also saw the badger -> both accepted.
    rows = [Vote(1, u, sp, 1, DAY, SHOT)
            for u, sp in [(1, FOX), (1, BADGER), (2, FOX), (2, BADGER), (3, FOX)]]
    stats, totals = compute_expertise_stats(rows)
    assert totals['series'] == 1
    # recall is per accepted species: users 1 and 2 named both, user 3 missed the badger
    assert (stats[1]['n'], stats[1]['hits']) == (2, 2)
    assert (stats[3]['n'], stats[3]['hits']) == (2, 1)


def test_a_species_nobody_else_saw_counts_against_precision():
    rows = [Vote(1, u, sp, 1, DAY, SHOT)
            for u, sp in [(1, FOX), (1, ROE), (2, FOX), (3, FOX)]]
    stats, _ = compute_expertise_stats(rows, mode='species')
    assert (stats[ROE]['said'], stats[ROE]['said_hits']) == (1, 0)
    assert (stats[FOX]['said'], stats[FOX]['said_hits']) == (3, 3)
