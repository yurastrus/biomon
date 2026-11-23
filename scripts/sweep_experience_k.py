"""Experience constant k in the weight confirmed / (n + k), read-only (2026-11-23).

k = 1 is the deployed rule. A larger k keeps newcomers at a low weight for
longer. For each k at the deployed threshold (1.49, AI 0.5 x score only for
a named species): backtest on cached votes (people per series, closed by the
first person, contradicted, needing more votes) and the share of
(person, species) records at which one person + DeepFaune may close.

    venv/Scripts/python -m scripts.sweep_experience_k
"""
import scripts.weighted_consensus_model as wcm
from scripts.probe_agreed_consensus import build, crossfit
from scripts.probe_weighted_consensus import DEFAULT_CACHE, fetch
from scripts.sweep_agreed_threshold import measure

KS = (1, 2, 3, 5, 10, 20)
THRESHOLD = 1.49


def main():
    items, _ = build(fetch(DEFAULT_CACHE, False))
    W = crossfit(items)
    print(f'threshold {THRESHOLD}; weight = confirmed / (n + k)')
    print(f'{"k":>3} {"people/series":>13} {"closed by 1st %":>15} {"need more %":>11} '
          f'{"contradicted %":>14} {"records solo":>12} {"n for 0.99 at 100%":>18}')
    for k in KS:
        wcm.experience_factor = lambda n, k=k: n / (n + k)
        for w in W.values():
            w.__dict__.pop('_cache', None)
        m = measure(items, W, lambda w, v, ai: wcm.rule_margin(
            w, v, ai, threshold=THRESHOLD, use_ai=True, ai_mode='score_half', ai_named_only=True))
        any_w = next(iter(W.values()))
        solo = sum(1 for u in any_w.said for s in any_w.said[u]
                   if any_w.human(u, s) >= THRESHOLD - 0.5 - 1e-9)
        total = sum(len(any_w.said[u]) for u in any_w.said)
        n_min = 99 * k   # confirmed = n: n / (n + k) >= 0.99
        print(f'{k:>3} {m["people"]:13.3f} {m["first"]:15.1f} {m["more"]:11.2f} '
              f'{m["contra"] if m["contra"] is not None else float("nan"):14.2f} '
              f'{solo:>5}/{total:<6} {n_min:>18}')


if __name__ == '__main__':
    main()
