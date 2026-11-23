"""Weighted-vote consensus study on the real CT votes (read-only).

What it answers (2026-11-23, owner's question): if each vote is weighted by
the verifier's record on that species, with a penalty for small records,
  1. which penalty predicts the NEXT person's answer best on unseen series
     (5-fold cross-fitting by series, mean log-loss of the next vote);
  2. what weights novices and experts would get;
  3. per closing rule: how many votes a series needs and how often it closes
     on the wrong species (simulation; votes beyond the real ones are drawn
     from the fitted model), compared with today's rule;
  4. on series where real people disagreed (3+ voters): does the rule, fed
     the real votes in their real order, land on the final majority;
  5. how many already closed two-vote series each rule would have held open.

Nothing is written to the database. Votes are cached in a local pickle
(--cache, default instance/weighted_consensus_votes.pkl; --refresh refetches).
Model and rules: scripts/weighted_consensus_model.py.

Usage:
    venv/Scripts/python -m scripts.probe_weighted_consensus [--refresh] [--sim N] [--seed S]
"""
import argparse
import math
import os
import pickle
import random
import time
from collections import Counter, defaultdict

import numpy as np

from scripts.weighted_consensus_model import (
    Model, Penalty, Stats, first_close, majority, next_vote_logloss,
    rule_current, rule_posterior, rule_sum, simulate)

DEFAULT_CACHE = os.path.join('instance', 'weighted_consensus_votes.pkl')
FOLDS = 5

VOTES_SQL = """
SELECT p.observation_id AS obs, o.status, i.user_id AS u, i.species_id AS sp,
       min(i.created_at) AS t
FROM identifications i
JOIN photos p ON p.id = i.photo_id
JOIN observations o ON o.id = p.observation_id
WHERE o.status IN ('completed', 'archived', 'pending')
  AND i.species_id IS NOT NULL
GROUP BY p.observation_id, o.status, i.user_id, i.species_id
"""

# The AI's answer for a series: the same rule as ai_runner.SERIES_PICK_ORDER_SQL
# (best model level, then newest model; 'empty' only if no frame has an animal;
# else the species on most frames). Plus when the AI result existed.
AI_SQL = """
WITH agg AS (
  SELECT observation_id, model_id, prediction_species_id AS sp,
         count(*) AS n_frames, max(prediction_score) AS best_score,
         min(processed_at) AS at
  FROM ai_predictions
  GROUP BY observation_id, model_id, prediction_species_id)
SELECT DISTINCT ON (agg.observation_id)
       agg.observation_id, agg.sp, agg.best_score, agg.at, m.name, lvl.code
FROM agg
JOIN ai_models m ON m.id = agg.model_id
LEFT JOIN ai_model_levels lvl ON lvl.id = m.level_id
ORDER BY agg.observation_id, COALESCE(lvl.accuracy_rank, 0) DESC, m.id DESC,
         (COALESCE(agg.sp, 0) = -1), agg.n_frames DESC,
         agg.best_score DESC NULLS LAST, agg.sp DESC NULLS LAST
"""


def fetch(cache, refresh):
    if os.path.exists(cache) and not refresh:
        with open(cache, 'rb') as fh:
            data = pickle.load(fh)
        if 'ai' in data:
            return data
    from sqlalchemy import text
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.camera_traps.database import get_ct_session, close_ct_session
        s = get_ct_session()
        try:
            s.execute(text('SET TRANSACTION READ ONLY'))
            rows = s.execute(text(VOTES_SQL)).fetchall()
            ai_rows = s.execute(text(AI_SQL)).fetchall()
            names = {r[0]: (r[1] or r[2]) for r in s.execute(text(
                'SELECT id, common_name_ua, scientific_name FROM species'))}
            s.rollback()
        finally:
            close_ct_session()
    by_obs = defaultdict(list)
    status = {}
    for obs, st, u, sp, t in rows:
        by_obs[obs].append((t, u, sp))
        status[obs] = st
    data = {'series': {}, 'times': {}, 'names': names,
            'fetched': time.strftime('%Y-%m-%d %H:%M'),
            # obs -> (species_id or None, best_score, processed_at, model, level)
            'ai': {r[0]: tuple(r[1:]) for r in ai_rows}}
    for obs, rows_ in by_obs.items():
        rows_.sort(key=lambda r: (r[0], r[1]))
        data['series'][obs] = (status[obs], [(u, sp) for _t, u, sp in rows_])
        data['times'][obs] = [t for t, _u, _sp in rows_]
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, 'wb') as fh:
        pickle.dump(data, fh)
    return data


def clean(data):
    """Drop series where one person named several species (single-label model)."""
    out, dropped = {}, 0
    for obs, (st, votes) in data['series'].items():
        users = [u for u, _s in votes]
        if len(users) != len(set(users)):
            dropped += 1
            continue
        out[obs] = (st, votes)
    return out, dropped


def fold_of(obs):
    return (obs * 2654435761) % 2 ** 32 % FOLDS


PENALTIES = (
    [Penalty('raw', r_max=0.99)]
    + [Penalty('wilson', z=z, r_max=0.99) for z in (1.0, 1.96)]
    + [Penalty('beta_class', m=m, r_max=0.99) for m in (1, 3, 10, 30, 100)]
    + [Penalty('beta_skill', m=m, r_max=0.99) for m in (1, 3, 10, 30, 100)]
    + [Penalty('beta_novice', m=m, m_user=mu, r_max=0.99)
       for m in (3, 10) for mu in (10, 50, 200)]
    + [Penalty('beta_novice', m=3, m_user=50, r_max=c) for c in (0.98, 0.995, 0.999)]
)

# experience buckets: graded votes of the NEXT voter in the training folds
BUCKETS = ((0, 50), (50, 500), (500, 5000), (5000, 10 ** 9))


def crossfit_models(series, classes, penalty):
    by_fold = defaultdict(list)
    for obs, (_st, votes) in series.items():
        by_fold[fold_of(obs)].append(votes)
    models = {}
    for f in range(FOLDS):
        train = [v for g, vs in by_fold.items() if g != f for v in vs]
        models[f] = Model(Stats(train), classes, penalty)
    return models, by_fold


def part1(series, classes):
    print('\n=== 1. Which small-sample penalty predicts the next vote best ===')
    print('Mean log-loss of the next real vote on series the model never saw '
          '(5-fold); lower is better. "agree" = predicted vs actual share of next '
          'votes equal to the current leader.')
    print('Columns after "real agree": log-loss on votes of voters with that many '
          'graded votes in the training folds.')
    print(f'{"penalty":32} {"logloss":>8} {"n":>7} {"pred agree":>10} {"real agree":>10}'
          + ''.join(f' {f"<{hi}" if hi < 10**9 else f">={lo}":>9}' for lo, hi in BUCKETS))
    results = []
    bucket_n = None
    for pen in PENALTIES:
        models, by_fold = crossfit_models(series, classes, pen)
        tot, n, calib = 0.0, 0, []
        bl, bn = Counter(), Counter()
        for f, votes_list in by_fold.items():
            m = models[f]
            ll, k, c = next_vote_logloss(m, votes_list)
            tot += ll * k
            n += k
            calib += c
            for u, loss in m.last_per_user:
                g = m.stats.user_totals(u)[1] if u in m.stats.N else 0
                b = next(i for i, (lo, hi) in enumerate(BUCKETS) if lo <= g < hi)
                bl[b] += loss
                bn[b] += 1
        bucket_n = bn
        pa = sum(p for p, _ in calib) / len(calib)
        ra = sum(1 for _, h in calib if h) / len(calib)
        results.append((tot / n, pen, calib))
        print(f'{pen.label():32} {tot / n:8.4f} {n:7d} {pa:10.3f} {ra:10.3f}'
              + ''.join(f' {bl[b] / bn[b] if bn[b] else float("nan"):9.4f}'
                        for b in range(len(BUCKETS))))
    print('votes per bucket: ' + ', '.join(f'{BUCKETS[b]}: {bucket_n[b]}'
                                           for b in range(len(BUCKETS))))
    results.sort(key=lambda r: r[0])
    best = results[0]
    print(f'\nBest: {best[1].label()}')
    print('Calibration of the best (predicted P(next vote = leader) vs observed):')
    bins = [0, .5, .8, .9, .95, .98, .99, 1.01]
    for lo, hi in zip(bins, bins[1:]):
        sel = [(p, h) for p, h in best[2] if lo <= p < hi]
        if sel:
            print(f'  {lo:4.2f}-{min(hi, 1):4.2f}: n={len(sel):6d}  '
                  f'predicted {np.mean([p for p, _ in sel]):.3f}  '
                  f'observed {np.mean([h for _, h in sel]):.3f}')
    return best[1], results


def part1b(series, classes):
    print('\n=== 1b. Cold start: each verifier scored by a model that never saw them ===')
    print("Leave-one-verifier-out. Log-loss on that verifier's votes as the next "
          'voter, averaged over VERIFIERS (each person counts once), so the many '
          'small newcomers weigh as much as the few heavy hitters.')
    pens = [p for p in PENALTIES if p.kind != 'raw']
    users = sorted({u for _st, v in series.values() for u, _s in v})
    per_pen = defaultdict(list)
    for u in users:
        train = [[x for x in v if x[0] != u] for _st, v in series.values()]
        test = [v for _st, v in series.values() if any(x[0] == u for x in v)]
        st = Stats(train)
        for pen in pens:
            m = Model(st, classes, pen)
            next_vote_logloss(m, test)
            losses = [loss for uu, loss in m.last_per_user if uu == u]
            if losses:
                per_pen[pen.label()].append(sum(losses) / len(losses))
    print(f'{"penalty":32} {"mean over verifiers":>20} {"median":>8}')
    for lab, vals in sorted(per_pen.items(), key=lambda kv: np.mean(kv[1])):
        print(f'{lab:32} {np.mean(vals):20.4f} {np.median(vals):8.4f}')


def part2(series, classes, names, penalties):
    print('\n=== 2. Weights per verifier (most frequent class: their r on it) ===')
    st = Stats([v for _s, v in series.values()])
    models = {p.label(): Model(st, classes, p) for p in penalties}
    users = sorted(st.N, key=lambda u: sum(st.N[u].values()))
    head = f'{"user":>5} {"graded":>7} {"overall":>8} {"class":24} {"H/N":>11}'
    print(head + ''.join(f' {lab[:18]:>18}' for lab in models))
    for u in users:
        h, n = st.user_totals(u)
        c = st.N[u].most_common(1)[0][0]
        hc, nc = st.H[u][c], st.N[u][c]
        line = (f'{u:>5} {n:7d} {h / n if n else 0:8.3f} {str(names.get(c, c))[:24]:24} '
                f'{hc:>5}/{nc:<5}')
        for lab, m in models.items():
            line += f' {m.row(u)[m.idx[c]]:18.3f}'
        print(line)
    # a hypothetical newcomer with 10/10 on the most common class
    print('\nNewcomer who got 10 of 10 right on the commonest class '
          '(and nothing else):')
    top_class = st.T.most_common(1)[0][0]
    fake = -999
    st.N[fake][top_class] = 10
    st.H[fake][top_class] = 10
    for lab, m in ((p.label(), Model(st, classes, p)) for p in penalties):
        print(f'  {lab:32} r = {m.row(fake)[m.idx[top_class]]:.3f}   '
              f'unseen class r = {m.row(-1000)[m.idx[top_class]]:.3f}')
    del st.N[fake], st.H[fake]


def rules_for(model):
    rules = [('current: 2 agree, >half', rule_current)]
    for t in (1.8, 1.96):
        rules.append((f'sum of accuracies >= {t}',
                      lambda v, t=t: rule_sum(model, v, threshold=t)))
    for tau in (0.95, 0.98, 0.99, 0.995, 0.999):
        rules.append((f'posterior >= {tau}',
                      lambda v, tau=tau: rule_posterior(model, v, tau=tau)))
    return rules


def part3(series, classes, penalty, sim_n, seed):
    print(f'\n=== 3. Simulated closing ({penalty.label()}) ===')
    print('Real votes first, then extra voters drawn by activity, answering from '
          'the fitted model. Truth drawn from the model posterior given all real '
          'votes. Same world for every rule.')
    rng = np.random.default_rng(seed)
    models, by_fold = crossfit_models(series, classes, penalty)
    act = Counter(u for _st, v in series.values() for u, _s in v)
    users = list(act)
    probs = np.array([act[u] for u in users], float)
    probs /= probs.sum()
    sample = defaultdict(list)
    keys = list(series)
    random.Random(seed).shuffle(keys)
    for obs in keys[:sim_n]:
        sample[fold_of(obs)].append(series[obs][1])
    agg = defaultdict(lambda: Counter())
    for f, votes_list in sample.items():
        m = models[f]
        for name, rule in rules_for(m):
            r = simulate(m, m, votes_list, rule, (users, probs), rng)
            for k, v in r.items():
                agg[name][k] += v * (r['n'] if k != 'n' else 1)
    print(f'{"rule":28} {"votes/series":>12} {"wrong %":>8} {"need extra %":>12} '
          f'{"open after 10 %":>15}')
    for name, a in agg.items():
        n = a['n']
        print(f'{name:28} {a["votes"] / n:12.3f} {100 * a["error"] / n:8.3f} '
              f'{100 * a["extra"] / n:12.2f} {100 * a["open"] / n:15.2f}')


def part4(series, classes, penalty):
    print(f'\n=== 4. Real disagreements: series with 3+ voters ({penalty.label()}) ===')
    print('Real votes in real order. "final" = majority of ALL its voters; '
          'series without a unique majority are skipped.')
    models, _ = crossfit_models(series, classes, penalty)
    rows = defaultdict(Counter)
    for obs, (_st, votes) in series.items():
        if len(votes) < 3:
            continue
        final = majority(votes)
        if final is None:
            continue
        m = models[fold_of(obs)]
        for name, rule in rules_for(m):
            k, sp = first_close(rule, votes)
            r = rows[name]
            r['n'] += 1
            if sp is None:
                r['open'] += 1
            else:
                r['closed'] += 1
                r['k'] += k
                r['early'] += k < len(votes)
                r['agree'] += sp == final
                r['early_wrong'] += (k < len(votes) and sp != final)
    print(f'{"rule":28} {"series":>6} {"closed %":>8} {"agrees w/ final %":>17} '
          f'{"mean k":>7} {"closed early, wrong":>19}')
    for name, r in rows.items():
        c = r['closed'] or 1
        print(f'{name:28} {r["n"]:6d} {100 * r["closed"] / r["n"]:8.1f} '
              f'{100 * r["agree"] / c:17.2f} {r["k"] / c:7.2f} {r["early_wrong"]:19d}')


def part5(series, classes, penalty, names):
    print(f'\n=== 5. Closed two-vote series that each rule would hold open '
          f'({penalty.label()}) ===')
    models, _ = crossfit_models(series, classes, penalty)
    held = defaultdict(Counter)
    total = Counter()
    for obs, (st, votes) in series.items():
        if st == 'pending' or len(votes) != 2:
            continue
        sp = votes[0][1]
        total[sp] += 1
        m = models[fold_of(obs)]
        for name, rule in rules_for(m)[1:]:
            if rule(votes) is None:
                held[name][sp] += 1
    n = sum(total.values())
    print(f'{n} closed series with exactly two votes')
    for name, c in held.items():
        print(f'  {name:28} held open: {sum(c.values()):6d} ({100 * sum(c.values()) / n:5.2f} %)')
    best = 'posterior >= 0.99'
    if best in held:
        print(f'\nBy species for "{best}" (top 15 by count held):')
        for sp, k in held[best].most_common(15):
            print(f'  {str(names.get(sp, sp))[:30]:30} {k:6d} of {total[sp]:6d} '
                  f'({100 * k / total[sp]:5.1f} %)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=DEFAULT_CACHE)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--sim', type=int, default=20000, help='series in the simulation')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--parts', default='12345')
    args = ap.parse_args()

    t0 = time.time()
    data = fetch(args.cache, args.refresh)
    series, dropped = clean(data)
    names = data['names']
    classes = sorted({s for _st, v in series.values() for _u, s in v})
    nv = Counter(len(v) for _st, v in series.values())
    print(f'Votes fetched {data["fetched"]}; {len(series)} series '
          f'({dropped} multi-species dropped); voters per series: '
          + ', '.join(f'{k}:{nv[k]}' for k in sorted(nv)))
    print(f'Statuses: {dict(Counter(st for st, _v in series.values()))}; '
          f'{len(classes)} classes; '
          f'{len({u for _s, v in series.values() for u, _x in v})} verifiers')

    best = Penalty('beta_class', m=10, r_max=0.995)
    if '1' in args.parts:
        best, _ = part1(series, classes)
    if 'c' in args.parts:
        part1b(series, classes)
    if '2' in args.parts:
        part2(series, classes, names,
              [Penalty('raw'), Penalty('wilson', z=1.96), Penalty('beta_class', m=3),
               Penalty('beta_novice', m=3, m_user=50), best])
    if '3' in args.parts:
        part3(series, classes, best, args.sim, args.seed)
    if '4' in args.parts:
        part4(series, classes, best)
    if '5' in args.parts:
        part5(series, classes, best, names)
    print(f'\n[{time.time() - t0:.0f} s]')


if __name__ == '__main__':
    main()
