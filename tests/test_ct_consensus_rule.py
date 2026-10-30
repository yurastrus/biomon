# SPDX-License-Identifier: AGPL-3.0-only
"""The multi-species consensus rule (app/camera_traps/consensus.py).

Three properties matter more than any single example:
  1. on single-species data (every verifier names one species, as all
     production data did until 2026-10) the accepted set is exactly the old
     `rn = 1` winner, so no statistic moves;
  2. the SQL fragment and the Python helpers agree on every series;
  3. people, not rows, are counted: rows are copied onto every photo.
"""
import random

import pytest
from sqlalchemy import create_engine, text

from app.camera_traps.consensus import (
    accepted_species, consensus_ctes, has_majority, tally_votes)

SOURCE = "FROM identifications i JOIN photos p ON i.photo_id = p.id"


@pytest.fixture
def conn():
    engine = create_engine('sqlite:///:memory:')
    with engine.connect() as c:
        c.execute(text("CREATE TABLE photos (id INTEGER PRIMARY KEY, observation_id INTEGER)"))
        c.execute(text("CREATE TABLE identifications (id INTEGER PRIMARY KEY, photo_id INTEGER,"
                       " user_id INTEGER, species_id INTEGER, quantity INTEGER)"))
        yield c


def _load(conn, series):
    """series: {obs_id: (n_photos, [(user, species, qty), ...])}"""
    pid = 0
    for obs, (n_photos, votes) in series.items():
        photo_ids = []
        for _ in range(n_photos):
            pid += 1
            photo_ids.append(pid)
            conn.execute(text("INSERT INTO photos VALUES (:i, :o)"), {'i': pid, 'o': obs})
        for user, sp, qty in votes:
            for ph in photo_ids:  # the real app copies the answer onto every photo
                conn.execute(text(
                    "INSERT INTO identifications (photo_id, user_id, species_id, quantity)"
                    " VALUES (:p, :u, :s, :q)"), {'p': ph, 'u': user, 's': sp, 'q': qty})


def _sql_sets(conn, rank_order='vote_count DESC, max_quantity DESC, species_id DESC'):
    rows = conn.execute(text(
        "WITH " + consensus_ctes(SOURCE, rank_order) +
        " SELECT observation_id, species_id, rn, accepted, vote_count, voters"
        " FROM RankedConsensus")).fetchall()
    primary, accepted = {}, {}
    for obs, sp, rn, acc, _vc, _v in rows:
        if rn == 1:
            primary[obs] = sp
        if acc:
            accepted.setdefault(obs, set()).add(sp)
    return primary, accepted


def test_single_species_per_verifier_accepts_only_the_old_winner(conn):
    rng = random.Random(20261029)
    series = {}
    for obs in range(1, 400):
        users = rng.sample(range(1, 12), rng.randint(1, 6))
        votes = [(u, rng.choice([-1, 5, 7, 9, 11]), rng.randint(1, 4)) for u in users]
        series[obs] = (rng.randint(1, 4), votes)
    _load(conn, series)
    primary, accepted = _sql_sets(conn)
    for obs in series:
        assert accepted[obs] == {primary[obs]}, obs


def test_species_named_by_more_than_half_is_accepted(conn):
    # u1 fox+badger, u2 fox+badger, u3 fox: fox 3/3, badger 2/3 -> both
    _load(conn, {1: (3, [(1, 10, 1), (1, 20, 2), (2, 10, 1), (2, 20, 1), (3, 10, 1)])})
    _p, accepted = _sql_sets(conn)
    assert accepted[1] == {10, 20}


def test_species_named_by_exactly_half_is_not_accepted(conn):
    # 2 voters: fox by both, badger by one -> 1*2 > 2 is false
    _load(conn, {1: (2, [(1, 10, 1), (1, 20, 1), (2, 10, 1)])})
    primary, accepted = _sql_sets(conn)
    assert primary[1] == 10
    assert accepted[1] == {10}


def test_people_not_rows_are_counted(conn):
    # five photos, one verifier with two species: still one voter, not ten
    _load(conn, {1: (5, [(1, 10, 1), (1, 20, 1)])})
    rows = conn.execute(text(
        "WITH " + consensus_ctes(SOURCE) +
        " SELECT species_id, vote_count, voters FROM RankedConsensus ORDER BY 1")).fetchall()
    assert [tuple(r) for r in rows] == [(10, 1, 1), (20, 1, 1)]


def test_sole_verifier_with_two_species_gets_both(conn):
    # 1 voter: each species 1*2 > 1 -> both accepted (relevant once a moderator closes it)
    _load(conn, {1: (2, [(1, 10, 1), (1, 20, 3)])})
    _p, accepted = _sql_sets(conn)
    assert accepted[1] == {10, 20}


def test_null_species_votes_count_as_voters(conn):
    # "other" stored as NULL in 11 legacy rows: it is a vote, and a voter
    _load(conn, {1: (1, [(1, None, 1), (2, 10, 1), (3, 10, 1)])})
    rows = conn.execute(text(
        "WITH " + consensus_ctes(SOURCE) +
        " SELECT species_id, voters, accepted FROM RankedConsensus ORDER BY vote_count DESC")).fetchall()
    assert rows[0][0] == 10 and rows[0][1] == 3 and rows[0][2]
    assert not rows[1][2]


def test_sql_and_python_agree_on_random_multi_species_data(conn):
    rng = random.Random(7)
    series = {}
    for obs in range(1, 500):
        votes = []
        for u in rng.sample(range(1, 10), rng.randint(1, 5)):
            for sp in rng.sample([-1, 3, 4, 5, 6], rng.randint(1, 3)):
                votes.append((u, sp, rng.randint(1, 3)))
        series[obs] = (rng.randint(1, 3), votes)
    _load(conn, series)
    primary, accepted = _sql_sets(conn)
    for obs, (n_photos, votes) in series.items():
        per_species, voters = tally_votes([v for v in votes for _ in range(n_photos)])
        assert set(accepted_species(per_species, voters, primary[obs])) == accepted[obs], obs


def test_page_tie_break_does_not_change_accepted_species_with_majority(conn):
    # fox and badger both named by all three: a tie, and both are accepted
    # whatever ORDER BY a page uses, so every page shows the same pair.
    votes = [(u, sp, 1) for u in (1, 2, 3) for sp in (10, 20)]
    _load(conn, {1: (2, votes)})
    _p1, asc = _sql_sets(conn, 'vote_count DESC, max_quantity DESC, species_id')
    _p2, desc = _sql_sets(conn, 'vote_count DESC, max_quantity DESC, species_id DESC')
    assert asc[1] == desc[1] == {10, 20}


@pytest.mark.parametrize('votes,expected', [
    ([(1, 10, 1), (2, 10, 1)], True),                       # unanimous
    ([(1, 10, 1), (2, 20, 1)], False),                      # split
    ([(1, 10, 1), (1, 20, 1), (2, 10, 1), (2, 20, 1)], True),  # two people, both {A,B}
    ([(1, 10, 1), (1, 20, 1), (2, 30, 1)], False),          # 1 of 2 each
    ([(1, 10, 1), (2, 10, 1), (3, 20, 1)], True),           # 2 of 3
])
def test_has_majority_counts_people(votes, expected):
    per_species, voters = tally_votes(votes * 4)  # four photos
    assert has_majority(per_species, voters) is expected
