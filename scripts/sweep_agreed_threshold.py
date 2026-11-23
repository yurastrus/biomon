"""Threshold sweep for the owner's simple weighted rule (read-only, 2026-11-23).

    points(species) = DeepFaune score * 0.5 (if it named that species)
                    + sum over people who named it of precision * n/(n+1)
    close when points(leader) - points(second) >= threshold
          and at least one person voted

The threshold goes from 0 to --max in steps of --step. For each value it
replays the real votes (weights cross-fitted, as in probe_agreed_consensus)
and records, on finished series: people per series (lower bound), share
closed by the first person, share needing more votes than they got; and on
early closures: share contradicted by the later voters. The same for the
rule without DeepFaune. Writes a CSV and a PNG.

Usage:
    venv/Scripts/python -m scripts.sweep_agreed_threshold [--max 3.0] [--step 0.1]
"""
import argparse
import csv
import os
from collections import Counter

from scripts.probe_agreed_consensus import build, crossfit, first_close
from scripts.probe_weighted_consensus import DEFAULT_CACHE, fetch, fold_of
from scripts.weighted_consensus_model import majority, rule_current, rule_margin

BLUE, ORANGE = '#2a78d6', '#eb6834'
MIN_CHECKED = 1000
NAMED_ONLY = True   # agreed 2026-11-23; the first sweep ran with False
INK, INK2, GRID = '#0b0b0b', '#52514e', '#e4e3df'


def measure(items, W, rule):
    r = Counter()
    for obs, (st, votes, ai) in items.items():
        w = W[fold_of(obs)]
        k, sp = first_close(lambda v: rule(w, v, ai), votes)
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
    f = r['fin'] or 1
    return {'people': r['people'] / f, 'first': 100 * r['k1'] / f,
            'more': 100 * r['more'] / f, 'checked': r['checked'],
            'contra': 100 * r['contra'] / r['checked'] if r['checked'] else None}


def plot(rows, base, path, chosen=1.5):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    panels = [
        ('people', 'Людей на серію (нижня межа)', None),
        ('contra', 'Спростовано пізнішими голосами, %', None),
        ('first', 'Закрито першою людиною, %', None),
        ('more', 'Серій, яким бракує голосів, %', None),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    fig.patch.set_facecolor('#fcfcfb')
    for ax, (key, title, _) in zip(axes.flat, panels):
        ax.set_facecolor('#fcfcfb')
        for mode, color, label in (('ai', BLUE, 'люди + DeepFaune × 0,5'),
                                   ('people', ORANGE, 'лише люди')):
            ok = [r for r in rows if r['mode'] == mode and r[key] is not None
                  and (key != 'contra' or r['checked'] >= MIN_CHECKED)]
            xs = [r['t'] for r in ok]
            ys = [r[key] for r in ok]
            ax.plot(xs, ys, color=color, lw=2, marker='o', ms=4, label=label)
        if base.get(key) is not None:
            ax.axhline(base[key], color=INK2, lw=1, ls='--')
            ax.annotate('зараз (2 згодні)', xy=(0.01, base[key]), xycoords=('axes fraction', 'data'),
                        xytext=(0, 4), textcoords='offset points', color=INK2, fontsize=8)
        ax.set_title(title, color=INK, fontsize=11, loc='left')
        ax.grid(True, color=GRID, lw=0.8)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors=INK2, labelsize=9)
        for x in (1.0, 1.5, 2.0):
            ax.axvline(x, color=INK2, lw=0.8, ls=':')
        if chosen is not None:
            ax.axvline(chosen, color=INK, lw=1.6)
            ax.annotate(f'обрано {chosen:g}'.replace('.', ','), xy=(chosen, 0.98),
                        xycoords=('data', 'axes fraction'), xytext=(4, -10),
                        textcoords='offset points', color=INK, fontsize=9, fontweight='bold')
    top = axes[0, 0]
    for x0, x1, txt in ((0, 1.0, '1 людина'), (1.0, 1.5, '1 людина + AI'),
                        (1.5, 2.0, '2 людини'), (2.0, 3.0, '3+ людей')):
        top.annotate(txt, xy=((x0 + x1) / 2, 0.62), xycoords=('data', 'axes fraction'),
                     ha='center', color=INK2, fontsize=8)
    axes[0, 1].annotate(f'лише точки з ≥ {MIN_CHECKED} перевірок', xy=(0.99, 0.80),
                        xycoords='axes fraction', ha='right', color=INK2, fontsize=8)
    axes[0, 0].annotate('вище 2,0 дані не кажуть, скільки треба:\nкожна серія рахується як «отримала + 1»',
                        xy=(0.99, 0.05), xycoords='axes fraction', ha='right', color=INK2, fontsize=8)
    for ax in axes[1]:
        ax.set_xlabel('Поріг переваги (бали лідера − бали другого)', color=INK2, fontsize=9)
    axes[0, 0].legend(frameon=False, fontsize=9)
    fig.suptitle(
        'Підбір порогу закриття серії, реальні голоси CT (115 тис. серій)',
                 color=INK, fontsize=13, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max', type=float, default=3.0)
    ap.add_argument('--step', type=float, default=0.1)
    ap.add_argument('--out', default=os.path.join('logs', 'sweep_agreed_threshold_named_20261123'))
    args = ap.parse_args()
    data = fetch(DEFAULT_CACHE, False)
    items, _ = build(data)
    W = crossfit(items)
    base = measure(items, W, lambda w, v, ai: rule_current(v))
    rows = []
    steps = int(round(args.max / args.step))
    for i in range(steps + 1):
        t = round(i * args.step, 3)
        for mode in ('ai', 'people'):
            m = measure(items, W, lambda w, v, ai, t=t, mode=mode: rule_margin(
                w, v, ai, threshold=t, use_ai=(mode == 'ai'), ai_mode='score_half',
                ai_named_only=NAMED_ONLY))
            rows.append({'t': t, 'mode': mode, **m})
            print(f'{t:4.1f} {mode:6} people {m["people"]:.3f} first {m["first"]:6.2f}% '
                  f'more {m["more"]:6.2f}% checked {m["checked"]:6d} contra '
                  f'{m["contra"] if m["contra"] is not None else float("nan"):6.2f}%', flush=True)
    print(f'current rule: {base}')
    with open(args.out + '.csv', 'w', newline='', encoding='utf-8') as fh:
        wr = csv.DictWriter(fh, fieldnames=['t', 'mode', 'people', 'first', 'more',
                                            'checked', 'contra'])
        wr.writeheader()
        wr.writerows(rows)
    plot(rows, base, args.out + '.png')
    print(f'wrote {args.out}.csv and .png')


if __name__ == '__main__':
    main()
