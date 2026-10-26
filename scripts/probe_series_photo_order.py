"""Read-only probe: how often are photos inside a CT series ordered ambiguously?

The /identify viewer orders a series by `captured_at` only. When several
photos share the same second (burst mode, cameras that store whole seconds),
the order among them is whatever the DB returned, so an animal can "jump back".

Measures, on the real ct_db:
  1. share of series with >=2 photos sharing one captured_at;
  2. for those, whether original_filename order disagrees with photo id order
     (id order = upload order, which with the parallel /upload-fast pool is
     not the camera's order);
  3. per camera-model-ish proxy (filename prefix), tie rates;
  4. series where filename order disagrees with captured_at order even
     WITHOUT ties (camera clock vs counter disagree, or rounding).

Usage: venv/Scripts/python -m scripts.probe_series_photo_order
"""
import os
import re
from collections import Counter, defaultdict

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def natural_key(name):
    base = os.path.basename(name or '')
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', base)]


def prefix(name):
    base = os.path.basename(name or '')
    m = re.match(r'^([A-Za-z_\-]*)', base)
    return (m.group(1) or '<digits>').upper()


def main():
    eng = create_engine(os.environ['CT_DATABASE_URL'])
    with eng.connect() as c:
        rows = c.execute(text("""
            SELECT p.observation_id, p.id, p.captured_at, p.original_filename,
                   p.sequence_number, o.status
              FROM photos p JOIN observations o ON o.id = p.observation_id
             WHERE o.status IN ('pending', 'completed')
               AND p.captured_at > '1901-01-01'
        """)).fetchall()

    series = defaultdict(list)
    status = {}
    for obs, pid, ts, fn, seq, st in rows:
        series[obs].append((ts, pid, fn, seq))
        status[obs] = st

    multi = {k: v for k, v in series.items() if len(v) > 1}
    n_tie = n_tie_disagree = n_notie_disagree = 0
    sub_second = 0
    by_prefix = Counter()
    by_prefix_tie = Counter()
    examples_tie, examples_notie = [], []
    for obs, ph in multi.items():
        if any(p[0].microsecond for p in ph):
            sub_second += 1
        pre = prefix(ph[0][2])
        by_prefix[pre] += 1
        ts_counts = Counter(p[0] for p in ph)
        has_tie = any(v > 1 for v in ts_counts.values())
        by_ts_id = [p[1] for p in sorted(ph, key=lambda p: (p[0], p[1]))]
        by_fn = [p[1] for p in sorted(ph, key=lambda p: natural_key(p[2]))]
        by_ts_fn = [p[1] for p in sorted(ph, key=lambda p: (p[0], natural_key(p[2])))]
        if has_tie:
            n_tie += 1
            by_prefix_tie[pre] += 1
            if by_ts_id != by_ts_fn:
                n_tie_disagree += 1
                if len(examples_tie) < 5:
                    examples_tie.append((obs, sorted(ph, key=lambda p: (p[0], p[1]))))
        elif by_ts_id != by_fn:
            n_notie_disagree += 1
            if len(examples_notie) < 5:
                examples_notie.append((obs, sorted(ph, key=lambda p: (p[0], p[1]))))

    print(f"series with >1 photo (pending/completed): {len(multi)}")
    print(f"  with sub-second timestamps:              {sub_second}")
    print(f"  with >=2 photos in the same second:      {n_tie} ({n_tie / max(len(multi), 1):.1%})")
    print(f"    ...where id order != filename order:   {n_tie_disagree}")
    print(f"  no ties but filename order != time order: {n_notie_disagree}")
    print("\nfilename prefix: series / with ties")
    for pre, n in by_prefix.most_common(15):
        print(f"  {pre:12s} {n:7d} {by_prefix_tie[pre]:7d} ({by_prefix_tie[pre] / n:.0%})")
    for title, ex in (("TIE examples (shown order by captured_at,id)", examples_tie),
                      ("NO-TIE disagreement examples", examples_notie)):
        print(f"\n{title}:")
        for obs, ph in ex:
            print(f"  obs {obs} [{status[obs]}]")
            for ts, pid, fn, seq in ph:
                print(f"     {ts}  id={pid}  seq={seq}  {os.path.basename(fn)}")


if __name__ == '__main__':
    main()
