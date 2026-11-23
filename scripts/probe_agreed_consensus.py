"""Backtest of the owner's agreed weighted-vote rule on real CT votes (read-only).

Rule (agreed 2026-11-23, see weighted_consensus_model.AgreedWeights):
  person's vote  = precision on that species * n / (n + 1)
  DeepFaune vote = its accuracy on that species at that confidence
                   * (1 - how much people copy its mistakes)
  votes for other species are subtracted; the series closes when the
  leader is ahead by >= threshold and at least one person voted.

Weights are cross-fitted (5 folds by series): a series is judged with
weights learned on the other folds. Human votes are replayed in their real
order; DeepFaune, when it has an answer, is assumed present from the start
(as it is now: the worker runs at upload).

How errors are measured without a ground truth: a series the rule closes
EARLY (before its last real vote) is checked against the people who voted
AFTER the closing vote (their majority). "contradicted" = they named
another species. This is the only independent check the data allows; a
contradicting later vote can itself be the wrong one, so read it as an
upper-side proxy, next to the same proxy for a single person.

Usage:
    venv/Scripts/python -m scripts.probe_agreed_consensus [--parts wrcs]
"""
import argparse
import time
from collections import Counter, defaultdict

from scripts.probe_weighted_consensus import DEFAULT_CACHE, FOLDS, fetch, fold_of
from scripts.weighted_consensus_model import (
    AgreedWeights, SCORE_BINS, UNIDENTIFIABLE, majority, rule_current, rule_margin)

THRESHOLDS = (1.4, 1.45, 1.5, 1.6, 1.9)


def _naive(t):
    return t.replace(tzinfo=None) if t is not None and t.tzinfo is not None else t


def build(data):
    """obs -> (status, votes, ai) with ai = (species, score, shown[]) or None."""
    out, dropped = {}, 0
    for obs, (st, votes) in data['series'].items():
        users = [u for u, _s in votes]
        if len(users) != len(set(users)):
            dropped += 1
            continue
        ai = data['ai'].get(obs)
        if ai is not None:
            sp, score, at = ai[0], ai[1], _naive(ai[2])
            times = data['times'][obs]
            shown = tuple(at is not None and _naive(t) is not None and at <= _naive(t)
                          for t in times)
            ai = (sp, score, shown)
        out[obs] = (st, votes, ai)
    return out, dropped


def crossfit(items):
    by_fold = defaultdict(list)
    for obs, (_st, votes, ai) in items.items():
        by_fold[fold_of(obs)].append((votes, ai))
    return {f: AgreedWeights([x for g, xs in by_fold.items() if g != f for x in xs])
            for f in range(FOLDS)}


def first_close(rule, votes):
    for k in range(1, len(votes) + 1):
        sp = rule(votes[:k])
        if sp is not None:
            return k, sp
    return None, None


def part_weights(items, names):
    w = AgreedWeights([(v, ai) for _st, v, ai in items.values()])
    nm = lambda s: str(names.get(s, s))[:24]
    print('\n=== Weights learned on all data ===')
    print(f'Copying overall (excess over base rate): {w.copy_all:.3f}')
    print('\nDeepFaune: copy rate by species it named (wrong AI shown before the vote):')
    print(f'{"species":24} {"copies/votes":>13} {"base":>6} {"copy":>6} {"smoothed":>9}')
    for s, (c, v, b, cr) in sorted(w.copy_raw.items(), key=lambda kv: -kv[1][1])[:20]:
        print(f'{nm(s):24} {c:>6}/{v:<6} {b:6.3f} {cr:6.3f} {w.copy[s]:9.3f}')
    print('\nDeepFaune weight by species and confidence (n series, accuracy, weight):')
    labels = [f'{SCORE_BINS[i]:.2f}-{min(SCORE_BINS[i + 1], 1):.2f}'
              for i in range(len(SCORE_BINS) - 1)]
    print(f'{"species":24}' + ''.join(f' {lab:>20}' for lab in labels))
    tot = Counter()
    for (s, b), n in w.ai_n.items():
        tot[s] += n
    for s, _n in tot.most_common(20):
        line = f'{nm(s):24}'
        for b in range(len(labels)):
            n = w.ai_n[(s, b)]
            if n:
                sc = (SCORE_BINS[b] + SCORE_BINS[b + 1]) / 2
                line += f' {n:6d} {w.ai_hit[(s, b)] / n:5.3f} {w.ai(s, sc):5.3f}'
            else:
                line += f' {"":>20}'
        print(line)
    print('\nPeople: weight on their most named species, and on Empty (-1):')
    print(f'{"user":>5} {"species":24} {"n":>6} {"prec":>6} {"weight":>7}   '
          f'{"n empty":>7} {"w empty":>7}')
    for u in sorted(w.said, key=lambda u: sum(w.said[u].values())):
        s, n = w.said[u].most_common(1)[0]
        ne = w.said[u][-1]
        print(f'{u:>5} {nm(s):24} {n:6d} {w.hit[u][s] / n:6.3f} {w.human(u, s):7.3f}   '
              f'{ne:7d} {w.human(u, -1):7.3f}')


def rules(w):
    out = [('current: 2 agree, >half', lambda v, ai: rule_current(v)),
           ('one person alone (proxy)', lambda v, ai: v[0][1] if len(v) == 1 else None)]
    for t in THRESHOLDS:
        out.append((f'margin >= {t} people only',
                    lambda v, ai, t=t: rule_margin(w, v, ai, threshold=t, use_ai=False, ai_mode='copy', ai_named_only=False)))
        out.append((f'margin >= {t} + DeepFaune',
                    lambda v, ai, t=t: rule_margin(w, v, ai, threshold=t, use_ai=True, ai_mode='copy', ai_named_only=False)))
        for mode in ('score_half', 'acc_half'):
            out.append((f'margin >= {t} + DF {mode}',
                        lambda v, ai, t=t, mode=mode: rule_margin(
                            w, v, ai, threshold=t, use_ai=True, ai_mode=mode,
                            ai_named_only=False)))
    return out


def part_replay(items, names, by_species_for):
    print('\n=== Replay of real votes (weights cross-fitted) ===')
    print('Finished series: mean people per series (if the rule needs more votes '
          'than the series got, counted as got+1, a lower bound), share closed by '
          'the 1st person, share needing more votes than they got.')
    print('Early closures: closed before the last real vote; checked against the '
          'majority of the later voters.')
    W = crossfit(items)
    stats = defaultdict(Counter)
    per_sp = defaultdict(Counter)
    for obs, (st, votes, ai) in items.items():
        w = W[fold_of(obs)]
        for name, rule in rules(w):
            k, sp = first_close(lambda v: rule(v, ai), votes)
            r = stats[name]
            if st != 'pending' and len(votes) >= 2:
                r['fin'] += 1
                r['people'] += k if k is not None else len(votes) + 1
                r['k1'] += (k == 1)
                r['more'] += (k is None)
            if k is not None and k < len(votes):
                later = majority(votes[k:])
                if later is not None:
                    r['checked'] += 1
                    r['contra'] += (later != sp)
                    if name == by_species_for:
                        per_sp[sp]['checked'] += 1
                        per_sp[sp]['contra'] += (later != sp)
            if name == by_species_for and st != 'pending' and len(votes) >= 2:
                s0 = majority(votes) or votes[0][1]
                per_sp[s0]['fin'] += 1
                per_sp[s0]['k1'] += (k == 1)
                per_sp[s0]['more'] += (k is None)
    print(f'\n{"rule":34} {"people/series":>13} {"closed by 1st %":>15} '
          f'{"need more %":>11} {"early checked":>13} {"contradicted %":>14}')
    for name, r in stats.items():
        f = r['fin'] or 1
        c = r['checked']
        print(f'{name:34} {r["people"] / f:13.3f} {100 * r["k1"] / f:15.2f} '
              f'{100 * r["more"] / f:11.2f} {c:13d} '
              f'{(100 * r["contra"] / c) if c else float("nan"):14.2f}')
    print(f'\nBy species for "{by_species_for}" (finished series >= 2 people; '
          f'top 25 by count):')
    print(f'{"species":28} {"series":>7} {"closed by 1st %":>15} {"need more %":>11} '
          f'{"early checked":>13} {"contradicted %":>14}')
    for sp, r in sorted(per_sp.items(), key=lambda kv: -kv[1]['fin'])[:25]:
        f = r['fin'] or 1
        c = r['checked']
        print(f'{str(names.get(sp, sp))[:28]:28} {r["fin"]:7d} {100 * r["k1"] / f:15.2f} '
              f'{100 * r["more"] / f:11.2f} {c:13d} '
              f'{(100 * r["contra"] / c) if c else float("nan"):14.2f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=DEFAULT_CACHE)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--parts', default='wr')
    ap.add_argument('--species-for', default='margin >= 1.9 + DeepFaune')
    args = ap.parse_args()
    t0 = time.time()
    data = fetch(args.cache, args.refresh)
    items, dropped = build(data)
    with_ai = sum(1 for _s, _v, ai in items.values() if ai is not None and ai[0] is not None)
    shown = sum(1 for _s, _v, ai in items.values() if ai is not None and ai[2] and ai[2][0])
    print(f'Votes fetched {data["fetched"]}; {len(items)} series ({dropped} multi-species '
          f'dropped); {with_ai} with a DeepFaune species answer; '
          f'{shown} with DeepFaune shown before the first vote')
    if 'w' in args.parts:
        part_weights(items, data['names'])
    if 'r' in args.parts:
        part_replay(items, data['names'], args.species_for)
    print(f'\n[{time.time() - t0:.0f} s]')


if __name__ == '__main__':
    main()
