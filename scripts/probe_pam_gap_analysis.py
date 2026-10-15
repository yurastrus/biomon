"""Read-only probe of the PAM gap analysis on a real pam_db.

Runs fetch_gap_data for the whole database (admin view, reference model) and
prints timings, the effect of the confidence threshold, and a summary per grid
scale. Writes nothing.

    venv/Scripts/python -m scripts.probe_pam_gap_analysis [taxon]
"""
import json
import sys
import time

from app import create_app


def main():
    taxon = sys.argv[1] if len(sys.argv) > 1 else 'birds'
    app = create_app()
    with app.app_context():
        from app.pam import gap_analysis as ga
        from app.pam.utils import get_pam_db_connection
        conn = get_pam_db_connection()
        try:
            t0 = time.perf_counter()
            raw = ga.fetch_gap_data(conn, '1=1', {}, taxon=taxon)
            print(f"fetch: {time.perf_counter() - t0:.1f}s  locations={len(raw.locations)} "
                  f"with_effort={len(raw.effort_days)} species={len(raw.species)} "
                  f"recorder_days={sum(len(d) for d in raw.effort_days.values())} "
                  f"hours={sum(sum(m.values()) for m in raw.effort_minutes.values()) / 60:.0f} "
                  f"confirmed_rows={sum(len(v) for per in raw.confirmed.values() for v in per.values())}")
            for thr in (0.7, 0.8, 0.9, 0.95, 0.99):
                t1 = time.perf_counter()
                data = ga.apply_threshold(raw, thr)
                grid = ga.build_grid(data, 5000, include_empty=False)
                s = grid['summary']
                cells = grid['cells']
                est = [c for c in cells if c['estimable']]
                cov = sorted(c['coverage'] for c in est)
                ps = sorted(c['pool_sensitivity'] for c in cells if c['pool_sensitivity'] is not None)
                q1 = sum(c['Q1'] for c in est)
                sobs = sum(c['S_obs'] for c in est)
                print(f"thr {thr}: {time.perf_counter() - t1:.1f}s {s['threshold_effect']} "
                      f"estimable={len(est)} unstable={sum(c['unstable'] for c in est)} "
                      f"cov median={cov[len(cov) // 2] if cov else None} "
                      f"singleton share={q1 / sobs if sobs else 0:.2f} "
                      f"pool sens median={ps[len(ps) // 2] if ps else None}")
            data = ga.apply_threshold(raw, ga.DEFAULT_THRESHOLD)
            for res in (1000, 5000, 10000):
                t2 = time.perf_counter()
                grid = ga.build_grid(data, res)
                s = grid['summary']
                body = json.dumps(grid['cells'])
                print(f"{res // 1000:>2} km: {time.perf_counter() - t2:.1f}s "
                      f"cells={s['n_cells_with_data']} empty={s['n_empty_cells']} "
                      f"trunc={s['empty_truncated']} json={len(body) / 1e6:.1f} MB")
            tot = s['total']
            print('total:', {k: tot[k] for k in ('T', 'S_obs', 'chao2', 'coverage', 'hours',
                                                'months_covered', 'diel_hours', 'diel_night_hours')})
            for bw in (500, 1000, 5000):
                t3 = time.perf_counter()
                k = ga.build_kernel(data, bw)
                print(f"kernel {bw} m: {time.perf_counter() - t3:.1f}s pixels={len(k['pixels'])}")
            print('hardest to detect:', [(p['name'], p['p_unit'], p['recorder_days_for_95'])
                                         for p in s['pool'][:8]])
            print('top species:', [(o['name'], o['detection_days']) for o in s['species_options'][:8]])
        finally:
            conn.close()


if __name__ == '__main__':
    main()
