# SPDX-License-Identifier: AGPL-3.0-only
"""What the weighted closing rule would do to PENDING series right now (read-only).

Computes people's records from the current votes (same as
init_verifier_weights), takes DeepFaune's series answers, and reports for
every pending series with enough people whether the rule would close it,
and on which species. Finished series are not looked at. Nothing is written.

Usage:
    venv/Scripts/python -m scripts.probe_weighted_pending
"""
from collections import Counter

from sqlalchemy import text

from app import create_app
from app.camera_traps.consensus import (
    WEIGHTED_THRESHOLD_DEFAULT, AI_VOTE_FACTOR_DEFAULT, can_close, is_multi_species,
    tally_votes, verifier_weight, weighted_close)
from app.camera_traps.database import close_ct_session, get_ct_session
from app.camera_traps.verifier_weights import compute, series_ai_answers


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, help='override the configured threshold')
    ap.add_argument('--min-ids', type=int, help='override MIN_IDENTIFICATIONS')
    args = ap.parse_args()
    app = create_app()
    with app.app_context():
        cfg = app.config.get('CAMERA_TRAP_CONFIG', {})
        min_ids = args.min_ids or cfg.get('MIN_IDENTIFICATIONS', 3)
        threshold = args.threshold or cfg.get('WEIGHTED_CONSENSUS_THRESHOLD', WEIGHTED_THRESHOLD_DEFAULT)
        factor = cfg.get('AI_VOTE_FACTOR', AI_VOTE_FACTOR_DEFAULT)
        s = get_ct_session()
        try:
            s.execute(text('SET TRANSACTION READ ONLY'))
            graded = compute(s.connection())
            weights = {k: verifier_weight(c, n) for k, (n, c) in graded.items()}
            rows = s.execute(text("""
                SELECT DISTINCT p.observation_id, i.user_id, i.species_id, i.quantity
                FROM identifications i JOIN photos p ON p.id = i.photo_id
                JOIN observations o ON o.id = p.observation_id
                WHERE o.status = 'pending'""")).fetchall()
            by_obs = {}
            for obs, u, sp, q in rows:
                by_obs.setdefault(obs, []).append((u, sp, q))
            cand = {o: v for o, v in by_obs.items() if len({u for u, _s, _q in v}) >= min_ids}
            ai = series_ai_answers(s, cand)
            names = {r[0]: (r[1] or r[2]) for r in s.execute(text(
                'SELECT id, common_name_ua, scientific_name FROM species'))}
            s.rollback()
        finally:
            close_ct_session()

    out, old_rule = Counter(), Counter()
    by_species = Counter()
    by_people = Counter()
    for obs, votes in cand.items():
        pairs = {(u, sp) for u, sp, _q in votes}
        winner = weighted_close(pairs, lambda u, sp: weights.get((u, sp), 0.0),
                                ai.get(obs), threshold=threshold, ai_factor=factor)
        per, voters = tally_votes(votes)
        old = can_close(per, voters, is_multi_species(votes))
        old_rule[old] += 1
        out[winner is not None] += 1
        if winner is not None:
            by_species[names.get(winner, winner)] += 1
            by_people[len({u for u, _s, _q in votes})] += 1
    print(f'{len(by_obs)} pending series; {len(cand)} with >= {min_ids} people; '
          f'threshold {threshold}, AI factor {factor}; {len(graded)} records')
    print(f'  weighted rule would close now: {out[True]}')
    print(f'  majority rule would close now: {old_rule[True]} '
          f'(should be ~0: those were closed at their vote)')
    if by_people:
        print('  closing, by people in the series: ' + ', '.join(
            f'{k}: {v}' for k, v in sorted(by_people.items())))
    if by_species:
        print('  closing species: ' + ', '.join(f'{k}: {v}' for k, v in by_species.most_common(15)))


if __name__ == '__main__':
    main()
