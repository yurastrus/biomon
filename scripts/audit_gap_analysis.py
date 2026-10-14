"""Read-only scientific audit of the CT gap analysis on a real ct_db.

Checks the assumptions behind gap_metrics / gap_analysis: share of
unverified series, taxa that are not species, day-to-day autocorrelation
of records, the Q1=1/Q2=0 coverage artefact, day vs week detection
probabilities, per-site p heterogeneity, biotope assignment. Writes nothing.

    venv/Scripts/python -m scripts.audit_gap_analysis
"""
import sys
from collections import Counter, defaultdict
from datetime import timedelta

from app import create_app
from sqlalchemy import text

app = create_app()
with app.app_context():
    from app.camera_traps.database import get_ct_session, close_ct_session
    from app.camera_traps import gap_analysis as ga, gap_metrics as gm
    from app.camera_traps.routes import DOMESTIC_SPECIES_IDS
    s = get_ct_session()

    # 1. Observation status distribution and unverified-only days
    print('status:', s.execute(text("SELECT status, count(*) FROM observations GROUP BY 1 ORDER BY 2 DESC")).fetchall())
    r = s.execute(text("""
        WITH d AS (SELECT location_id, DATE(series_start_time) d,
                          BOOL_OR(status IN ('completed','archived')) v
                   FROM observations GROUP BY 1,2)
        SELECT v, count(*) FROM d GROUP BY v""")).fetchall()
    print('location-days verified/unverified-only:', r)

    # 2. Species with positive id that look like genus/higher rank
    rows = s.execute(text("""SELECT id, scientific_name, common_name_ua, category FROM species
                             WHERE id > 0 AND (scientific_name ILIKE '%% sp%%' OR scientific_name !~ ' '
                                   OR common_name_ua ILIKE '%%ві' OR common_name_ua ILIKE '%%ові')""")).fetchall()
    print('suspicious positive-id taxa:', rows)

    data = ga.fetch_gap_data(s, '1=1', {}, exclude_species_ids=DOMESTIC_SPECIES_IDS)
    print('mammal species in scope:', sorted((v['scientific']) for v in data.species.values()))

    # 3. Autocorrelation: share of detection-days followed by a detection the next day, same loc/species
    nxt = tot = 0
    for lid, per in data.detections.items():
        for sid, days in per.items():
            for d in days:
                tot += 1
                nxt += (d + timedelta(days=1)) in days
    print(f'P(detected next day | detected today) = {nxt / tot:.3f}  (n={tot})')
    # compare with marginal p
    p = ga.species_detection_probabilities(data, 'day')
    w = sum(p[sid] * sum(len(per.get(sid, ())) for per in data.detections.values()) for sid in p) / tot
    print(f'detection-weighted marginal p = {w:.3f}')

    # 4. How often coverage==1 comes from Q1==1,Q2==0 (A=0 artefact) vs Q1==0
    for unit in ('day', 'week'):
        g = ga.build_grid(data, 5000, unit=unit, include_empty=False)
        est = [c for c in g['cells'] if c['estimable']]
        c1 = [c for c in est if c['coverage'] == 1]
        art = sum(1 for c in c1 if c['Q1'] == 1 and c['Q2'] == 0)
        print(unit, 'estimable', len(est), 'coverage==1', len(c1), 'of which Q1=1,Q2=0', art,
              'Q1=0', sum(1 for c in c1 if c['Q1'] == 0), 'S_obs<=2', sum(1 for c in c1 if c['S_obs'] <= 2))

    # 5. Day vs week p consistency: 1-(1-p_day)^7 vs p_week
    pw = ga.species_detection_probabilities(data, 'week', 3)
    pd = ga.species_detection_probabilities(data, 'day', 3)
    for sid in sorted(pd, key=pd.get)[:6]:
        print(f"{data.species[sid]['name_uk']:<22} p_day={pd[sid]:.4f} 1-(1-pd)^7={1-(1-pd[sid])**7:.3f} p_week={pw.get(sid, 0):.3f}")

    # 6. Site heterogeneity of p for lynx-like rare species: per-site p distribution
    units_by_loc = {lid: len(days) for lid, days in data.effort_days.items()}
    for sid in sorted(pd, key=pd.get)[:3]:
        per_site = [(len(per[sid]) / units_by_loc[lid], units_by_loc[lid]) for lid, per in data.detections.items() if sid in per]
        per_site.sort()
        print(data.species[sid]['name_uk'], 'sites', len(per_site), 'per-site p median',
              round(per_site[len(per_site)//2][0], 4), 'sites with 1 detection', sum(1 for ps, n in per_site if ps * n == 1))

    # 7. Biotope assignment coverage
    nb = Counter(len(data.biotopes.get(l, [])) for l in data.effort_days)
    print('biotopes per location (with effort):', dict(nb))
    close_ct_session()
