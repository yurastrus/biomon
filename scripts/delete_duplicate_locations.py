"""One-off: delete the 13 ct_db locations named 'duplicate_*' — but ONLY if
they truly hold no real data (observations / deployments / service_visits /
photos). Self-guarding: the whole thing runs in one transaction and rolls back
if any target location unexpectedly has real data.

Cascade tables (location_biotopes, location_institutions, location_stats,
location_monthly_activity) are removed automatically by ON DELETE CASCADE.
Only empty upload_batches must be removed manually first (NO ACTION FK).

Run:  venv/Scripts/python -m scripts.delete_duplicate_locations
"""
import os
import re

from sqlalchemy import create_engine, text

ENV = os.path.join(os.path.dirname(__file__), os.pardir, '.env')


def _ct_url():
    with open(ENV, encoding='utf-8') as f:
        for line in f:
            if line.strip().startswith('CT_DATABASE_URL'):
                return re.search(r"=\s*['\"]?([^'\"\n]+)", line).group(1)
    raise SystemExit('CT_DATABASE_URL not found in .env')


REAL_DATA_SQL = text("""
SELECT l.id, l.name,
  (SELECT count(*) FROM observations   o WHERE o.location_id = l.id) AS observations,
  (SELECT count(*) FROM deployments    d WHERE d.location_id = l.id) AS deployments,
  (SELECT count(*) FROM service_visits s WHERE s.location_id = l.id) AS service_visits,
  (SELECT count(*) FROM photos p JOIN observations   o2 ON p.observation_id  = o2.id WHERE o2.location_id = l.id) AS photos_via_obs,
  (SELECT count(*) FROM photos p JOIN upload_batches b2 ON p.upload_batch_id = b2.id WHERE b2.location_id = l.id) AS photos_via_batch
FROM locations l
WHERE l.name ILIKE 'duplicate%'
ORDER BY l.id;
""")


def main():
    eng = create_engine(_ct_url())
    with eng.begin() as conn:               # single transaction — commits on clean exit
        rows = conn.execute(REAL_DATA_SQL).fetchall()
        if not rows:
            print("No 'duplicate%' locations found — nothing to do.")
            return

        ids = [r.id for r in rows]
        print(f"Matched {len(rows)} 'duplicate%' locations: {ids}\n")

        # ── GUARD: abort the whole transaction if ANY has real data ──
        unsafe = [r for r in rows if (r.observations or r.deployments or
                                      r.service_visits or r.photos_via_obs or
                                      r.photos_via_batch)]
        if unsafe:
            print("ABORTING — these locations hold real data (nothing deleted):")
            for r in unsafe:
                print(f"  #{r.id} {r.name}: obs={r.observations} depl={r.deployments} "
                      f"visits={r.service_visits} photos={r.photos_via_obs + r.photos_via_batch}")
            raise SystemExit(1)   # rolls back the transaction

        # ── Empty upload_batches (NO ACTION FK) — delete only if photo-less ──
        empty_batches = conn.execute(text("""
            SELECT b.id FROM upload_batches b
            WHERE b.location_id = ANY(:ids)
              AND NOT EXISTS (SELECT 1 FROM photos p WHERE p.upload_batch_id = b.id)
        """), {'ids': ids}).fetchall()
        batch_ids = [b.id for b in empty_batches]
        if batch_ids:
            conn.execute(text("DELETE FROM upload_batches WHERE id = ANY(:bids)"),
                         {'bids': batch_ids})
            print(f"Deleted {len(batch_ids)} empty upload_batch(es): {batch_ids}")

        # Report cascade rows about to go (informational).
        for tbl in ('location_biotopes', 'location_institutions',
                    'location_stats', 'location_monthly_activity'):
            n = conn.execute(text(f"SELECT count(*) FROM {tbl} WHERE location_id = ANY(:ids)"),
                             {'ids': ids}).scalar()
            print(f"  cascade {tbl}: {n} row(s)")

        # ── Delete the locations (CASCADE handles the rest) ──
        deleted = conn.execute(text("DELETE FROM locations WHERE id = ANY(:ids)"),
                               {'ids': ids}).rowcount
        print(f"\nDeleted {deleted} location(s).")

        # Final sanity re-check inside the same transaction.
        left = conn.execute(text("SELECT count(*) FROM locations WHERE name ILIKE 'duplicate%'")).scalar()
        print(f"Remaining 'duplicate%' locations after delete: {left}")
        print("Committing transaction.")


if __name__ == '__main__':
    main()
