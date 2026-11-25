"""Read-only probe of the PAM local-export plan on a real pam_db.

Runs the sampling query in export shape (no dedup, vote tally joined) for one
species over every location, once per verification subset and unit, and prints
counts, timings and the verification states found. Writes nothing (the
transaction is rolled back).

    venv/Scripts/python -m scripts.probe_pam_export_plan "Bufo bufo" [sample_size]
"""
import sys
import time
from collections import Counter

from sqlalchemy import text

from app import create_app


def main():
    species = sys.argv[1] if len(sys.argv) > 1 else 'Bufo bufo'
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    app = create_app()
    with app.app_context():
        from app.pam import pam_segment_sampling as pss
        from app.pam.utils import get_pam_db_connection
        conn = get_pam_db_connection()
        try:
            location_ids = [r[0] for r in conn.execute(text("SELECT location_id FROM locations"))]
            baseline = pss.run_stratified_sample(species, location_ids, sample_size=size,
                                                 conn=conn, model_id=1)
            print(f"{species}: server-upload sample (dedup on) = {len(baseline)}")
            for unit in ('segments', 'files'):
                for verification in pss.VERIFICATION_FILTERS:
                    for verdict in ('any', 'confirmed', 'rejected'):
                        if verification == 'unverified' and verdict != 'any':
                            continue
                        t0 = time.perf_counter()
                        plan = pss.run_stratified_sample(
                            species, location_ids, sample_size=size, conn=conn,
                            model_id=1, skip_sampled=False,
                            verification=verification, verdict=verdict,
                            per_recording=(unit == 'files'))
                        took = time.perf_counter() - t0
                        states = Counter(p['verification_state'] for p in plan)
                        recordings = len({p['recording_id'] for p in plan})
                        print(f"  {unit:8} {verification:10} {verdict:9} n={len(plan):5} "
                              f"recordings={recordings:5} {took:5.1f}s  {dict(states)}")
        finally:
            conn.rollback()
            conn.close()


if __name__ == '__main__':
    main()
