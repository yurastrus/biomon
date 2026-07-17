# SPDX-License-Identifier: AGPL-3.0-only
"""Rebuild detection_verification_map (dvm) directly from the AUTHORITATIVE
segments.detection_id, replacing the legacy filename/heuristic linkage that the
pam_detailed charts read.

Verdict rule (agreed with the user — Option A):
  * source = segments with detection_id NOT NULL and status IN ('completed','archived').
  * votes AGGREGATED per detection across duplicate segments (sum).
  * verification_result:
      - tot >= 2  -> crowd consensus, 2/3 threshold (1 / 0 / NULL);
      - tot  = 1 AND archived -> the single recorded vote IS the authoritative
        external verdict (legacy hand-verified batch): 1 (pos) or 0;
      - else NULL.
  * positive_votes = summed positive; segment_id = representative (most votes, then id).

Run:  --report  (dry-run, shows diff vs current dvm)
      --apply   (DELETE + INSERT inside one transaction)
"""
import argparse, re
from pathlib import Path
import psycopg2

PROPOSED = """
WITH agg AS (
  SELECT s.detection_id,
         SUM(COALESCE(s.verification_count,0))     AS tot,
         SUM(COALESCE(s.positive_verifications,0))  AS pos,
         bool_or(s.status='archived')              AS any_archived,
         (array_agg(s.id ORDER BY COALESCE(s.verification_count,0) DESC, s.id))[1] AS rep_seg
  FROM segments s
  WHERE s.detection_id IS NOT NULL
    AND s.status IN ('completed','archived')
  GROUP BY s.detection_id
)
SELECT detection_id, rep_seg AS segment_id,
  CASE WHEN tot>=2 AND pos::decimal/tot >= 2.0/3 THEN 1
       WHEN tot>=2 AND pos::decimal/tot <= 1.0/3 THEN 0
       WHEN tot=1 AND any_archived THEN pos
       ELSE NULL END AS verification_result,
  pos AS positive_votes
FROM agg
"""

def connect():
    env={}
    for line in Path('.env').read_text(encoding='utf-8').splitlines():
        m=re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
        if m: env[m.group(1)]=m.group(2)
    return psycopg2.connect(env['PAM_DATABASE_URL'])

def main():
    ap=argparse.ArgumentParser(); g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--report',action='store_true'); g.add_argument('--apply',action='store_true')
    a=ap.parse_args()
    conn=connect(); conn.autocommit=False; cur=conn.cursor(); cur.execute("SET statement_timeout='240s'")

    cur.execute("CREATE TEMP TABLE _newdvm ON COMMIT DROP AS " + PROPOSED)
    cur.execute("SELECT COUNT(*) FROM _newdvm"); proposed=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM detection_verification_map"); current=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM _newdvm n WHERE NOT EXISTS (SELECT 1 FROM detection_verification_map m WHERE m.detection_id=n.detection_id)"); added=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM detection_verification_map m WHERE NOT EXISTS (SELECT 1 FROM _newdvm n WHERE n.detection_id=m.detection_id)"); removed=cur.fetchone()[0]
    cur.execute("""SELECT COUNT(*) FROM detection_verification_map m JOIN _newdvm n USING(detection_id)
                   WHERE m.verification_result IS DISTINCT FROM n.verification_result"""); changed=cur.fetchone()[0]
    cur.execute("SELECT verification_result, COUNT(*) FROM _newdvm GROUP BY 1 ORDER BY 1"); dist=cur.fetchall()

    print("=== dvm rebuild ===")
    print(f"  mode:            {'APPLY' if a.apply else 'REPORT (dry-run)'}")
    print(f"  current dvm rows:  {current}")
    print(f"  proposed dvm rows: {proposed}")
    print(f"  added (newly colored detections): {added}")
    print(f"  removed (stale/wrong links dropped): {removed}")
    print(f"  result changed on kept detections: {changed}")
    print(f"  proposed result distribution: {dist}  (1=correct,0=incorrect)")

    if a.apply:
        cur.execute("DELETE FROM detection_verification_map")
        cur.execute("""INSERT INTO detection_verification_map
                       (detection_id, segment_id, verification_result, positive_votes)
                       SELECT detection_id, segment_id, verification_result, positive_votes FROM _newdvm""")
        print(f"\n  APPLIED: dvm replaced with {cur.rowcount} rows.")
        conn.commit(); print("  committed.")
    else:
        conn.rollback(); print("\n  Dry-run only.")
    conn.close()

if __name__=='__main__':
    main()
