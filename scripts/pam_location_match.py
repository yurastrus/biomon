# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only: match unlinked PAM segment location codes to the locations registry
by coordinates (haversine, 50 m threshold).

Code->coord source: the audio archive folder names
  F:/1_Acoustic_recordings/1_Hearable_recordings  (see scripts/pam_archive_scan.py),
which are the authoritative per-code coordinates. Two codes are spread across
several loggers (ambiguous) and are handled manually.
Run: venv/Scripts/python -m scripts.pam_location_match
"""
import re
from math import radians, sin, cos, asin, sqrt
from pathlib import Path
import psycopg2

# code -> (lat, lon), dominant archive folder. Only codes among unlinked segments.
CODE_COORDS = {
    "S3": (49.93254, 23.72918),
    "ROZTOCH-POND": (49.93254, 23.72918),
    "S2": (49.89491, 23.75235),
    "ROZTOCH-FEN": (49.93382, 23.75125),
    "S1": (49.88575, 23.76230),
    "K1": (49.90937, 23.74553),
    "ROZT-STAVKY": (49.93378, 23.77084),
    "S6": (49.96404, 23.66153),
    "S4": (49.94602, 23.69649),
    "DAY": (49.93254, 23.72918),
    "SYCH": (49.92222, 23.77287),
    "S5": (49.95569, 23.67268),
    "VYNNYKY-FIE": (49.83130, 24.14172),
    "MAIDAN": (49.97598, 23.65874),
    "S-24": (49.91589, 23.75869),
    "ROZTOCH-8410": (49.82080, 24.12245),
    "VYNNYKY6": (49.82443, 24.11840),
    "ARDEA-COLONY": (49.90986, 23.74972),
    "ROZTOCH-G-21": (49.94602, 23.69649),
    "VYNNYKY5": (49.82080, 24.12245),
}
# Ambiguous — spread across several loggers in the archive; leave to user.
AMBIGUOUS = {"CBM-UA-ROZ-2", "ROZTOCH-CBM"}

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * r * asin(sqrt(a))

env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
    if m:
        env[m.group(1)] = m.group(2)
conn = psycopg2.connect(env['PAM_DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT location_id, location_name, lat, lon FROM locations WHERE lat IS NOT NULL AND lon IS NOT NULL")
regs = cur.fetchall()
cur.execute("SELECT location_name, COUNT(*) FROM segments WHERE recording_id IS NULL GROUP BY location_name")
counts = dict(cur.fetchall())
conn.close()

THRESH = 50.0
print(f"{'CODE':14} {'segs':>5}  {'ok':2} {'loc_id':>6} {'dist_m':>7}  registry_name")
print("-" * 92)
seg_ok = 0
for code, (lat, lon) in sorted(CODE_COORDS.items(), key=lambda kv: -counts.get(kv[0], 0)):
    best = min(regs, key=lambda r: haversine_m(lat, lon, r[2], r[3]))
    dist = haversine_m(lat, lon, best[2], best[3])
    n = counts.get(code, 0)
    ok = "OK" if dist <= THRESH else "!!"
    if dist <= THRESH:
        seg_ok += n
    print(f"{code:14} {n:5d}  {ok} {best[0]:6d} {dist:7.1f}  {best[1]}")

print("\n-- AMBIGUOUS (code spread across multiple loggers, needs a decision) --")
seg_amb = 0
for code in sorted(AMBIGUOUS):
    n = counts.get(code, 0); seg_amb += n
    print(f"  {code:14} {n:5d} segs")

print(f"\nMatched <=50m: {seg_ok} segs across {len(CODE_COORDS)} codes; "
      f"ambiguous: {seg_amb} segs across {len(AMBIGUOUS)} codes.")
