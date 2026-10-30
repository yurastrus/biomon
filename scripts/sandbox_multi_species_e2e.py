# SPDX-License-Identifier: AGPL-3.0-only
"""End-to-end check of multi-species identification on the SANDBOX ct_db.

Writes identifications, so it refuses to run anywhere but the sandbox cluster
(server port 5544; tunnel: ssh -N -L 5545:localhost:5544 yurastrus.dev).

Two pseudo-verifiers (user ids 990001/990002, no main-DB accounts needed) name
the same two species for one untouched pending series. Then: the series must
close through the real consensus code, and every reader must report both
species for it: export (two occurrence rows with stable IDs), top species,
distribution map, activity heatmap, location stats, AI was_correct.

    venv/Scripts/python -m scripts.sandbox_multi_species_e2e
"""
import os
import sys

os.environ['CT_DATABASE_URL'] = 'postgresql://ct_user@localhost:5545/ct_db'

from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.models import User  # noqa: E402

U1, U2 = 990001, 990002
FOX, ROE = 10, 4   # Vulpes vulpes, Capreolus capreolus on biomon


def main():
    app = create_app()
    with app.app_context():
        from app.camera_traps.database import get_ct_engine, get_ct_session, close_ct_session
        from app.camera_traps.models import Identification, Observation
        from app.camera_traps.utils import check_consensus_for_observation
        from app.camera_traps.service_analytics import calculate_stats_for_location

        engine = get_ct_engine()
        with engine.connect() as c:
            if c.execute(text('SELECT inet_server_port()')).scalar() != 5544:
                sys.exit('REFUSING: not the sandbox')
            obs_id = c.execute(text("""
                SELECT o.id FROM observations o JOIN locations l ON l.id = o.location_id
                WHERE o.status = 'pending' AND l.is_valid IS NOT FALSE
                  -- the AI saw the SECOND species: was_correct must become true
                  AND EXISTS (SELECT 1 FROM ai_predictions ap
                              WHERE ap.observation_id = o.id AND ap.prediction_species_id = :roe)
                  AND NOT EXISTS (SELECT 1 FROM photos p JOIN identifications i ON i.photo_id = p.id
                                  WHERE p.observation_id = o.id)
                  AND (SELECT COUNT(*) FROM photos p WHERE p.observation_id = o.id) >= 2
                ORDER BY o.id LIMIT 1"""), {'roe': ROE}).scalar()
        print('series', obs_id)

        s = get_ct_session()
        try:
            obs = s.query(Observation).get(obs_id)
            day = obs.series_start_time.date().isoformat()
            loc = obs.location_id
            for uid in (U1, U2):
                for photo in obs.photos:
                    photo.identification_count = (photo.identification_count or 0) + 1
                    s.add(Identification(photo_id=photo.id, user_id=uid, species_id=FOX, quantity=2))
                    s.add(Identification(photo_id=photo.id, user_id=uid, species_id=ROE, quantity=1))
                s.flush()
                check_consensus_for_observation(obs_id, db_session=s)
                print(f'after verifier {uid}: status = {obs.status}')
            s.commit()
            calculate_stats_for_location(loc, s)
            s.commit()
        finally:
            close_ct_session()

        admin = User.query.filter(User.roles.any(name='admin')).order_by(User.id).first()
        cl = app.test_client()
        with cl.session_transaction() as sess:
            sess['_user_id'] = str(admin.id)
            sess['_fresh'] = True
        D = f'start_date={day}&end_date={day}'
        checks = []

        csv = cl.get(f'/uk/camera-traps/api/data-download?{D}&export_mode=consensus&filter_type=all').get_data(as_text=True)
        ids = [line.split(',')[0] for line in csv.splitlines() if f':observation:{obs_id}' in line]
        checks.append(('export: two rows, primary id plain, second with taxon',
                       sorted(ids) == sorted([f'URN:ctmon:WNBO-CT:observation:{obs_id}',
                                              f'URN:ctmon:WNBO-CT:observation:{obs_id}:taxon:{ROE}']), ids))
        agg = cl.get(f'/uk/camera-traps/api/data-download?{D}&export_mode=consensus&aggregation=location_day&filter_type=all').get_data(as_text=True)
        checks.append(('export location_day: both species of the series survive aggregation',
                       sum(1 for line in agg.splitlines() if f',{obs_id},' in line) == 2, None))

        top = cl.get(f'/uk/camera-traps/api/stats/top-species?{D}&locations={loc}').get_json()
        checks.append(('top species: both counted', len(top.get('labels', [])) >= 2, top))

        for sp in (FOX, ROE):
            dm = cl.get(f'/uk/camera-traps/api/stats/distribution-map?{D}&species_id={sp}').get_json()
            hit = [l for l in dm.get('locations', []) if l['id'] == loc]
            checks.append((f'distribution map has location for species {sp}', bool(hit), hit))
            hm = cl.get(f'/uk/camera-traps/api/stats/activity-heatmap?{D}&species_id={sp}&scope_type=location&scope_id={loc}').get_json()
            checks.append((f'heatmap counts species {sp}', (hm or {}).get('total', 0) >= 1, (hm or {}).get('total')))

        with engine.connect() as c:
            row = c.execute(text('SELECT animal_observations, total_species FROM location_stats '
                                 'WHERE location_id = :l'), {'l': loc}).fetchone()
            rows_per_species = c.execute(text("""
                SELECT i.species_id, COUNT(*) FROM identifications i JOIN photos p ON p.id = i.photo_id
                WHERE p.observation_id = :o GROUP BY 1 ORDER BY 1"""), {'o': obs_id}).fetchall()
        checks.append(('location stats recomputed', row is not None, tuple(row) if row else None))
        with engine.connect() as c:
            wc = c.execute(text('SELECT prediction_species_id, was_correct FROM ai_predictions '
                                'WHERE observation_id = :o'), {'o': obs_id}).fetchall()
        checks.append(('AI that named the second accepted species counts as correct',
                       any(sp == ROE and w is True for sp, w in wc), [tuple(r) for r in wc]))
        print('rows per species:', [tuple(r) for r in rows_per_species])

        ok = True
        for name, passed, detail in checks:
            ok &= bool(passed)
            print(('PASS ' if passed else 'FAIL ') + name + ('' if passed else f'  -> {detail}'))
        print('ALL PASS' if ok else 'SOME FAILED')
        return obs_id


if __name__ == '__main__':
    main()
