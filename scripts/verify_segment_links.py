# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only: verify segment<->detection<->recording<->location integrity across
ALL fields. Run: venv/Scripts/python -m scripts.verify_segment_links"""
import re
from pathlib import Path
import psycopg2
env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
    if m: env[m.group(1)] = m.group(2)
conn = psycopg2.connect(env['PAM_DATABASE_URL']); cur = conn.cursor()
cur.execute("SET statement_timeout='240s'")
def show(t, sql, a=None):
    print("\n=== " + t + " ===")
    cur.execute(sql, a or ())
    cols=[d[0] for d in cur.description]; print(" | ".join(cols))
    for r in cur.fetchall(): print(" | ".join('' if v is None else str(v) for v in r))

show("linkage coverage after backfill", """
    SELECT COUNT(*) total,
      COUNT(recording_id) with_rec,
      COUNT(detection_id) with_det,
      COUNT(*) FILTER (WHERE recording_id IS NULL) still_unlinked
    FROM segments""")

# FULL consistency across every linked segment, using all fields
show("integrity of ALL linked segments (seg->det->rec)", """
    SELECT
      COUNT(*) total_linked,
      COUNT(*) FILTER (WHERE d.detection_id IS NULL) det_missing,
      COUNT(*) FILTER (WHERE d.recording_id <> s.recording_id) rec_id_mismatch,
      COUNT(*) FILTER (WHERE d.species_id <> s.species_id) species_mismatch,
      COUNT(*) FILTER (WHERE d.start_s IS DISTINCT FROM s.start_s) start_s_mismatch,
      COUNT(*) FILTER (WHERE abs(d.confidence - s.confidence_level) > 0.0015) conf_far,
      COUNT(*) FILTER (WHERE (r.datetime_start AT TIME ZONE 'UTC') <> (s.recorded_date + s.recorded_time)) datetime_mismatch
    FROM segments s
    LEFT JOIN detections d ON s.detection_id = d.detection_id
    LEFT JOIN recordings r ON s.recording_id = r.recording_id
    WHERE s.recording_id IS NOT NULL""")

# Location consistency for the BACKFILLED codes: recording.location matches code map
show("location consistency for backfilled codes", """
    WITH cm(code,loc) AS (VALUES
      ('S3',9),('ROZTOCH-POND',9),('S2',2),('ROZTOCH-FEN',59),('S1',1),('K1',4),
      ('ROZT-STAVKY',33),('S6',7),('S4',6),('DAY',9),('ROZTOCH-G-21',6),('SYCH',12),
      ('S5',3),('VYNNYKY-FIE',26),('MAIDAN',8),('S-24',58),('ROZTOCH-8410',24),
      ('VYNNYKY6',25),('VYNNYKY5',24),('ARDEA-COLONY',62))
    SELECT COUNT(*) checked,
      COUNT(*) FILTER (WHERE r.location_id = cm.loc) loc_ok,
      COUNT(*) FILTER (WHERE r.location_id <> cm.loc) loc_wrong
    FROM segments s
    JOIN cm ON cm.code = s.location_name
    JOIN recordings r ON s.recording_id = r.recording_id
    WHERE s.detection_id IS NOT NULL""")

# What remains unlinked, by code
show("still-unlinked segments by code", """
    SELECT location_name, COUNT(*) n FROM segments
    WHERE recording_id IS NULL GROUP BY location_name ORDER BY n DESC""")

# detection_id uniqueness on segments (shared detections)
show("segments sharing a detection_id", """
    SELECT COUNT(*) AS detections_shared_by_multiple_segments FROM (
      SELECT detection_id FROM segments WHERE detection_id IS NOT NULL
      GROUP BY detection_id HAVING COUNT(*)>1) q""")
conn.close()
