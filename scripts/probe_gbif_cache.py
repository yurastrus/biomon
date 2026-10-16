"""Read-only probe of the GBIF cache refresh on a real ct_db.

Fetches and aggregates GBIF records exactly as the refresh would, but writes
nothing (write=False), then prints what the cache would hold.

    venv/Scripts/python -m scripts.probe_gbif_cache
"""
import time
from collections import Counter

from app import create_app


def main():
    app = create_app()
    with app.app_context():
        from app.camera_traps.database import get_ct_engine
        from app.camera_traps import gbif_cache as gc
        t0 = time.perf_counter()
        s = gc.refresh_gbif_cache(get_ct_engine(), write=False,
                                  progress=lambda m: print('   ', m, flush=True))
        print(f"took {time.perf_counter() - t0:.0f}s", {k: s[k] for k in ('dropped_by_reason',
            'records_fetched', 'records_kept', 'records_dropped', 'species', 'boxes', 'taxon_queries', 'years')})
        counts, taxa = s['counts'], s['taxa']
        y0, y1 = gc.gbif_year_window()
        per_taxon = Counter()
        unc = Counter()
        for (t, e, n, y, u), c in counts.items():
            if y0 <= y <= y1:
                per_taxon[t] += c
                unc[u] += c
        print('window', (y0, y1), 'records', sum(per_taxon.values()), 'uncertainty buckets', dict(unc))
        on_cam = [k for k in per_taxon if taxa[k]['ct_species_id']]
        print('species in window', len(per_taxon), 'of them on cameras', len(on_cam))
        print('not on cameras:', [(taxa[k]['name_uk'], c) for k, c in per_taxon.most_common() if not taxa[k]['ct_species_id']])

        # The grid exactly as the page would draw it (admin view, mammals), in memory.
        from app.camera_traps import gap_analysis as ga
        from app.camera_traps.database import get_ct_session, close_ct_session
        from app.camera_traps.routes import DOMESTIC_SPECIES_IDS
        rows = Counter()
        for (t, e, n, y, u), c in counts.items():
            if y0 <= y <= y1:
                rows[(t, e, n, u)] += c
        gbif = gc.GbifData(taxa, [k + (c,) for k, c in rows.items()], None, (y0, y1))
        data = ga.fetch_gap_data(get_ct_session(), '1=1', {}, exclude_species_ids=DOMESTIC_SPECIES_IDS)
        close_ct_session()
        for res in (1000, 5000, 10000):
            grid = ga.build_grid(data, res, gbif=gbif)
            k = Counter(c.get('knowledge') for c in grid['cells'])
            missed = Counter(m['name'] for c in grid['cells'] if c['status'] == 'data' for m in c['gbif_missed'])
            print(f"{res // 1000:>2} km knowledge {dict(k)}; most 'in GBIF, missed by camera':", missed.most_common(6))


if __name__ == '__main__':
    main()
