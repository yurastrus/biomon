# SPDX-License-Identifier: AGPL-3.0-only
"""Backfill segments.recording_id / detection_id for LEGACY segments that the
canonical stem-based linker cannot match.

Why the canonical linker misses them (see app/pam/pam_segment_sampling.backfill_
segment_links): it matches a recording by filename stem (LOCATION_YYYYMMDD_HHMMSS).
These legacy segments fail that because the matching recording rows either store a
full Windows path in `filename`, or live under a DIFFERENT location-code than the
segment prefix (several codes -> one physical location). The reliable key is
recording.location_id + recording.datetime_start, then the detection by
(species_id, start_s), with confidence as a corroborating tie-break.

code->location_id map is from the audio archive folder coordinates + the legacy R
script, matched to the locations registry within 50 m (see scripts/
pam_location_match.py, scripts/pam_archive_scan.py).

Run from project root:
    venv/Scripts/python -m scripts.backfill_legacy_segment_links --report   # dry-run
    venv/Scripts/python -m scripts.backfill_legacy_segment_links --apply    # write

Idempotent: only touches segments with recording_id IS NULL.
"""
import argparse
import re
from pathlib import Path
import psycopg2

# code -> location_id (archive-verified, 0 m matches; 2 ambiguous codes excluded)
CODEMAP = [
    ('S3', 9), ('ROZTOCH-POND', 9), ('S2', 2), ('ROZTOCH-FEN', 59), ('S1', 1),
    ('K1', 4), ('ROZT-STAVKY', 33), ('S6', 7), ('S4', 6), ('DAY', 9),
    ('ROZTOCH-G-21', 6), ('SYCH', 12), ('S5', 3), ('VYNNYKY-FIE', 26),
    ('MAIDAN', 8), ('S-24', 58), ('ROZTOCH-8410', 24), ('VYNNYKY6', 25),
    ('VYNNYKY5', 24), ('ARDEA-COLONY', 62),
]
VALUES = ", ".join(f"('{c}',{l})" for c, l in CODEMAP)

# One matched detection per segment. Tie-break: confidence agreement first, then
# the recording with the fullest detection set (canonical import over the bare
# duplicate), then lowest recording_id.
MATCH_CTE = f"""
WITH cm(code, loc) AS (VALUES {VALUES}),
seg AS (
    SELECT s.id, cm.loc, s.species_id, s.start_s, s.confidence_level AS seg_conf,
           (s.recorded_date + s.recorded_time) AS dt
    FROM segments s JOIN cm ON cm.code = s.location_name
    WHERE s.recording_id IS NULL
),
cand AS (
    SELECT seg.id AS segment_id, seg.seg_conf,
           d.detection_id, d.recording_id, d.confidence AS det_conf,
           (round(d.confidence::numeric, 3) = seg.seg_conf) AS conf_match,
           (SELECT count(*) FROM detections dd WHERE dd.recording_id = r.recording_id) AS rec_ndet
    FROM seg
    JOIN recordings r ON r.location_id = seg.loc
                     AND (r.datetime_start AT TIME ZONE 'UTC') = seg.dt
    JOIN detections d ON d.recording_id = r.recording_id
                     AND d.species_id = seg.species_id
                     AND d.start_s = seg.start_s
),
pick AS (
    SELECT DISTINCT ON (segment_id)
           segment_id, detection_id, recording_id, seg_conf, det_conf, conf_match
    FROM cand
    ORDER BY segment_id, conf_match DESC, rec_ndet DESC, recording_id
)
"""

def connect():
    env = {}
    for line in Path('.env').read_text(encoding='utf-8').splitlines():
        m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
        if m:
            env[m.group(1)] = m.group(2)
    return psycopg2.connect(env['PAM_DATABASE_URL'])

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--report', action='store_true')
    g.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET statement_timeout='240s'")

    # Overall unlinked landscape
    cur.execute("SELECT COUNT(*) FROM segments WHERE recording_id IS NULL")
    total_unlinked = cur.fetchone()[0]

    # Build match set into a temp table for reuse
    cur.execute(f"CREATE TEMP TABLE _pick ON COMMIT DROP AS {MATCH_CTE} SELECT * FROM pick")
    cur.execute("SELECT COUNT(*) FROM _pick")
    matched = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM _pick WHERE conf_match")
    conf_ok = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT detection_id) FROM _pick")
    distinct_det = cur.fetchone()[0]

    print("=== Legacy segment link backfill ===")
    print(f"  mode:                 {'APPLY' if args.apply else 'REPORT (dry-run)'}")
    print(f"  total unlinked segs:  {total_unlinked}")
    print(f"  matched (rec+det):    {matched}")
    print(f"  confidence agrees:    {conf_ok}  ({matched-conf_ok} differ >3dp)")
    print(f"  distinct detections:  {distinct_det}  ({matched-distinct_det} shared)")

    # Confidence outliers (largest abs diff) — sanity
    cur.execute("""
        SELECT segment_id, detection_id, seg_conf, round(det_conf::numeric,4) det_conf,
               round(abs(det_conf - seg_conf)::numeric,4) AS d
        FROM _pick WHERE NOT conf_match ORDER BY d DESC LIMIT 12""")
    rows = cur.fetchall()
    if rows:
        print("\n  confidence mismatches (top abs diff):")
        print("  seg_id | det_id | seg_conf | det_conf | diff")
        for r in rows:
            print("   " + " | ".join(str(x) for x in r))

    if args.apply:
        cur.execute("""
            UPDATE segments s
            SET recording_id = p.recording_id, detection_id = p.detection_id
            FROM _pick p
            WHERE s.id = p.segment_id AND s.recording_id IS NULL
        """)
        print(f"\n  APPLIED: {cur.rowcount} rows updated.")
        conn.commit()
        print("  committed.")
    else:
        conn.rollback()
        print("\n  Dry-run only. Re-run with --apply to write.")
    conn.close()

if __name__ == '__main__':
    main()
