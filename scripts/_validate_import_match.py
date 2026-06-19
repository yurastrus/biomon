# SPDX-License-Identifier: AGPL-3.0-only
"""READ-ONLY: counts matches per location for a split-CSV
(filename + captured_at to the second). Writes nothing. Validates the
matching key and timezone assumptions against real data; also resolves
folder → location mapping.

Usage:
    venv/Scripts/python scripts/_validate_import_match.py <path_to_csv>
"""
import sys, csv, os, re
from datetime import datetime
from collections import Counter
from pathlib import Path
import psycopg2

if len(sys.argv) < 2:
    sys.exit("Usage: python scripts/_validate_import_match.py <path_to_csv>")
CSV_PATH = sys.argv[1]

env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    m = re.match(r"^([A-Z_]+)=['\"]?(.*?)['\"]?$", line.strip())
    if m:
        env[m.group(1)] = m.group(2)

# parse CSV keys
keys = set()
with open(CSV_PATH, encoding='utf-8-sig', newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        fn = (row['filename'] or '').strip().replace('\\', '/')
        try:
            dt = datetime.strptime(row['date'].strip(), '%Y:%m:%d %H:%M:%S').replace(microsecond=0)
        except ValueError:
            continue
        keys.add((os.path.basename(fn).lower(), dt))

print(f'CSV: {CSV_PATH}')
print(f'  unique (filename, sec) keys: {len(keys)}')

c = psycopg2.connect(env['CT_DATABASE_URL']); c.set_session(readonly=True, autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT lower(p.original_filename), date_trunc('second', p.captured_at), o.location_id, l.name
    FROM photos p
    JOIN observations o ON o.id = p.observation_id
    JOIN locations l ON l.id = o.location_id
""")
per_loc = Counter()
loc_name = {}
for fn, sec, loc_id, lname in cur.fetchall():
    sec = sec.replace(tzinfo=None)
    if (fn, sec) in keys:
        per_loc[loc_id] += 1
        loc_name[loc_id] = lname

print('  matches per location (top 5):')
if not per_loc:
    print('    !!! 0 matches — check the assumptions about time/names')
for loc_id, n in per_loc.most_common(5):
    pct = 100.0 * n / len(keys) if keys else 0
    print(f'    location_id={loc_id:5}  matches={n:6} ({pct:5.1f}%)  {loc_name[loc_id]}')
cur.close(); c.close()
