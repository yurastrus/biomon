"""What the simple weighted rule would say about already finished series (read-only).

Rule (owner, 2026-11-23): points = DeepFaune score x 0.5 + sum of
precision x n/(n+1) of the people who named the species; close when the
leader is ahead by >= threshold and a person voted. Weights are learned on
all data, as production would learn them if switched on now.

For every completed / archived series, with ALL its votes, it reports:
  closed, same species as today's consensus  - nothing changes
  closed, another species                    - the record would change
  not closed                                 - the series would need more votes
broken down by species, with example series ids.

Usage:
    venv/Scripts/python -m scripts.probe_threshold_on_finished [--threshold 1.5]
"""
import argparse
from collections import Counter, defaultdict

from app.camera_traps.consensus import tally_votes
from scripts.probe_agreed_consensus import build
from scripts.probe_weighted_consensus import DEFAULT_CACHE, fetch
from scripts.weighted_consensus_model import AgreedWeights, rule_margin


def today_species(votes):
    """Today's winner: most voters, then (no quantities here) species id DESC."""
    per, _n = tally_votes((u, s, 0) for u, s in votes)
    return max(per.items(), key=lambda kv: (kv[1][0], kv[0]))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, default=1.5)
    ap.add_argument('--ai-against-people', action='store_true',
                    help='old variant: AI points also for a species nobody named')
    args = ap.parse_args()
    data = fetch(DEFAULT_CACHE, False)
    names = data['names']
    nm = lambda s: str(names.get(s, s))[:28]
    items, _ = build(data)
    w = AgreedWeights([(v, ai) for _st, v, ai in items.values()])

    outcome = Counter()
    by_sp = defaultdict(Counter)
    changed = Counter()
    examples = defaultdict(list)
    open_by_n = Counter()
    lead_open = []
    for obs, (st, votes, ai) in items.items():
        if st == 'pending':
            continue
        today = today_species(votes)
        new = rule_margin(w, votes, ai, threshold=args.threshold, use_ai=True,
                          ai_mode='score_half', ai_named_only=not args.ai_against_people)
        if new is None:
            key = 'open'
            open_by_n[len(votes)] += 1
        elif new == today:
            key = 'same'
        else:
            key = 'changed'
            changed[(nm(today), nm(new))] += 1
        outcome[key] += 1
        by_sp[today][key] += 1
        if key != 'same' and len(examples[(key, today)]) < 3:
            examples[(key, today)].append(obs)

    total = sum(outcome.values())
    print(f'Finished series (completed + archived): {total}; threshold {args.threshold}; '
          f'AI only for named species: {not args.ai_against_people}')
    for key, label in (('same', 'closed, same species as today'),
                       ('changed', 'closed, ANOTHER species'),
                       ('open', 'not closed, would need more votes')):
        print(f'  {label:36} {outcome[key]:7d}  ({100 * outcome[key] / total:5.2f} %)')
    print('  not closed, by number of people it had: '
          + ', '.join(f'{k}: {v}' for k, v in sorted(open_by_n.items())))

    print('\nBy today\'s species (top 30 by series; % of that species):')
    print(f'{"species":28} {"series":>7} {"same %":>7} {"another %":>9} {"open %":>7}  open examples')
    for sp, c in sorted(by_sp.items(), key=lambda kv: -sum(kv[1].values()))[:30]:
        n = sum(c.values())
        ex = ','.join(map(str, examples[('open', sp)]))
        print(f'{nm(sp):28} {n:7d} {100 * c["same"] / n:7.2f} {100 * c["changed"] / n:9.2f} '
              f'{100 * c["open"] / n:7.2f}  {ex}')

    print('\nWhere the species would change (today -> new):')
    for (a, b), k in changed.most_common(25):
        print(f'  {a:28} -> {b:28} {k:5d}')
    print('\nExample series ids for changes:')
    for (key, sp), ids in examples.items():
        if key == 'changed':
            print(f'  {nm(sp):28} {ids}')


if __name__ == '__main__':
    main()
