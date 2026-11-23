# SPDX-License-Identifier: AGPL-3.0-only
"""Fine threshold sweep 1.40-1.50 for the weighted rule (read-only, 2026-11-23).

Two views per threshold, MIN_IDENTIFICATIONS 1:
  1. backtest on the cached votes (scripts/probe_agreed_consensus, cross-fitted
     weights): people per series, share closed by the first person, share
     contradicted by later voters;
  2. today's pending series on the live DB: how many the rule would close now,
     Empty vs animals (what the admin recount button would do).
Writes logs/sweep_threshold_fine_<date>.{csv,png}.

Usage:
    venv/Scripts/python -m scripts.sweep_threshold_fine
"""
import csv
import os
from collections import Counter

from sqlalchemy import text

BLUE, ORANGE, AQUA = '#2a78d6', '#eb6834', '#1baf7a'
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
OUT = os.path.join('logs', 'sweep_threshold_fine_20261123')
STEPS = [round(1.40 + i * 0.01, 2) for i in range(11)]


def backtest():
    from scripts.probe_agreed_consensus import build, crossfit
    from scripts.probe_weighted_consensus import DEFAULT_CACHE, fetch
    from scripts.sweep_agreed_threshold import measure
    from scripts.weighted_consensus_model import rule_margin
    items, _ = build(fetch(DEFAULT_CACHE, False))
    W = crossfit(items)
    out = {}
    for t in STEPS:
        out[t] = measure(items, W, lambda w, v, ai, t=t: rule_margin(
            w, v, ai, threshold=t, use_ai=True, ai_mode='score_half', ai_named_only=True))
        print(f'backtest {t:.2f}: {out[t]}', flush=True)
    return out


def pending_now():
    from app import create_app
    from app.camera_traps.consensus import verifier_weight, weighted_close
    from app.camera_traps.database import close_ct_session, get_ct_session
    from app.camera_traps.verifier_weights import compute, series_ai_answers
    app = create_app()
    with app.app_context():
        s = get_ct_session()
        try:
            s.execute(text('SET TRANSACTION READ ONLY'))
            graded = compute(s.connection())
            rows = s.execute(text("""
                SELECT DISTINCT p.observation_id, i.user_id, i.species_id
                FROM identifications i JOIN photos p ON p.id = i.photo_id
                JOIN observations o ON o.id = p.observation_id
                WHERE o.status = 'pending'""")).fetchall()
            by_obs = {}
            for obs, u, sp in rows:
                by_obs.setdefault(obs, set()).add((u, sp))
            ai = series_ai_answers(s, by_obs)
            s.rollback()
        finally:
            close_ct_session()
    weights = {k: verifier_weight(c, n) for k, (n, c) in graded.items()}
    out = {}
    for t in STEPS:
        c = Counter()
        for obs, pairs in by_obs.items():
            sp = weighted_close(pairs, lambda u, x: weights.get((u, x), 0.0),
                                ai.get(obs), threshold=t)
            if sp is not None:
                c['empty' if sp == -1 else 'animal'] += 1
        out[t] = c
        print(f'pending {t:.2f}: {dict(c)}', flush=True)
    return out, len(by_obs)


def plot(bt, pend, n_pending):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    fig.patch.set_facecolor(SURF)
    xs = STEPS
    panels = [
        (axes[0, 0], 'Людей на серію (історія)', [([bt[t]['people'] for t in xs], BLUE, None)]),
        (axes[0, 1], 'Спростовано пізнішими голосами, % (історія)',
         [([bt[t]['contra'] if bt[t]['checked'] >= 1000 else None for t in xs], BLUE, None)]),
        (axes[1, 0], 'Закрито першою людиною + DeepFaune, % (історія)',
         [([bt[t]['first'] for t in xs], BLUE, None)]),
        (axes[1, 1], 'Закрилось би зараз із ' + f'{n_pending:,}'.replace(',', ' ') + ' серій, що очікують',
         [([pend[t]['empty'] for t in xs], AQUA, 'Пусто'),
          ([pend[t]['animal'] for t in xs], ORANGE, 'тварини, люди, техніка')]),
    ]
    for ax, title, series in panels:
        ax.set_facecolor(SURF)
        for ys, color, label in series:
            pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, lw=2,
                    marker='o', ms=4, label=label)
        ax.set_title(title, color=INK, fontsize=11, loc='left')
        ax.grid(True, color=GRID, lw=0.8)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=9)
        ax.set_xticks(xs)
        ax.set_xticklabels([f'{x:.2f}'.replace('.', ',') for x in xs])
        if any(lab for _y, _c, lab in series):
            ax.legend(frameon=False, fontsize=9)
    axes[0, 1].axhline(0.4, color=INK2, lw=1, ls='--')
    axes[0, 1].annotate('2 людини (поріг 1,5): ~0,4 % за моделлю; на 1,50 перевірок замало',
                        xy=(0.02, 0.05),
                        xycoords='axes fraction', color=INK2, fontsize=8)
    for ax in axes[1]:
        ax.set_xlabel('Поріг переваги', color=INK2, fontsize=9)
    fig.suptitle('Поріг 1,40–1,50: людина + DeepFaune, MIN_IDENTIFICATIONS = 1',
                 color=INK, fontsize=13, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(OUT + '.png', dpi=130)


def main():
    bt = backtest()
    pend, n = pending_now()
    with open(OUT + '.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['threshold', 'people_per_series', 'closed_by_first_pct', 'contradicted_pct',
                    'checked', 'pending_close_empty', 'pending_close_animal'])
        for t in STEPS:
            w.writerow([t, round(bt[t]['people'], 4), round(bt[t]['first'], 2),
                        round(bt[t]['contra'], 3) if bt[t]['contra'] is not None else '',
                        bt[t]['checked'], pend[t]['empty'], pend[t]['animal']])
    plot(bt, pend, n)
    print(f'wrote {OUT}.csv and .png')


if __name__ == '__main__':
    main()
