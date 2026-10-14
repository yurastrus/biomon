"""Read-only probe of the camera-trap gap analysis on a real ct_db.

Runs fetch_gap_data for the whole database (admin view, no QC exclusions) and
prints timings and a summary per grid scale. Writes nothing.

    venv/Scripts/python -m scripts.probe_gap_analysis [taxon]
"""
import sys
import time

from app import create_app


def main():
    taxon = sys.argv[1] if len(sys.argv) > 1 else 'mammals'
    app = create_app()
    with app.app_context():
        from app.camera_traps.database import get_ct_session, close_ct_session
        from app.camera_traps import gap_analysis as ga
        from app.camera_traps.routes import DOMESTIC_SPECIES_IDS
        session = get_ct_session()
        try:
            t0 = time.perf_counter()
            data = ga.fetch_gap_data(session, '1=1', {}, taxon=taxon,
                                     exclude_species_ids=DOMESTIC_SPECIES_IDS)
            t1 = time.perf_counter()
            print(f"fetch: {t1 - t0:.1f}s  locations={len(data.locations)} "
                  f"with_effort={len(data.effort_days)} species={len(data.species)} "
                  f"camera_days={sum(len(d) for d in data.effort_days.values())}")
            for res in (1000, 5000, 10000):
                t2 = time.perf_counter()
                grid = ga.build_grid(data, res)
                s = grid['summary']
                cells = [c for c in grid['cells'] if c['status'] == 'data']
                est = [c for c in cells if c['estimable']]
                cov = sorted(c['coverage'] for c in est)
                print(f"{res // 1000:>2} km: {time.perf_counter() - t2:.1f}s cells={len(cells)} "
                      f"estimable={len(est)} empty={s['n_empty_cells']} trunc={s['empty_truncated']} "
                      f"coverage median={cov[len(cov) // 2] if cov else None}")
            tot = s['total']
            print('total:', {k: tot[k] for k in ('T', 'S_obs', 'chao2', 'coverage',
                                                'months_covered', 'extra_camera_days_cov95')})
            for bw in (1000, 5000):
                t3 = time.perf_counter()
                k = ga.build_kernel(data, bw)
                print(f"kernel {bw} m: {time.perf_counter() - t3:.1f}s pixels={len(k['pixels'])}")
            print('top species:', [(o['name'], o['detection_days']) for o in s['species_options'][:8]])
        finally:
            close_ct_session()


if __name__ == '__main__':
    main()
