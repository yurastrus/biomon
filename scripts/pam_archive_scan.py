# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only: scan the audio archive to map segment location codes (file prefix)
to coordinates (encoded in an ancestor folder name '... @ lat @ lon @').
Run: venv/Scripts/python -m scripts.pam_archive_scan"""
import os, re
from collections import defaultdict

ROOT = r"F:/1_Acoustic_recordings/1_Hearable_recordings"

# folder like: "... @ <name> @ <lat> @ <lon> @"  -> capture last two floats
COORD_RE = re.compile(r"@\s*([-\d]+\.\d+)\s*@\s*([-\d]+\.\d+)\s*@\s*$")
# filename: CODE_YYYYMMDD_HHMMSS.ext ; code = prefix before _<8digits>_<6digits>
NAME_RE = re.compile(r"^(.*?)_(\d{8})_(\d{6})", re.IGNORECASE)
AUDIO = ('.wav', '.flac')

def coord_of_path(path):
    """Nearest ancestor folder that encodes coords -> (lat, lon, folder_name)."""
    for part in reversed(path.split(os.sep)):
        m = COORD_RE.search(part)
        if m:
            name = part.split(" @ ")[1] if " @ " in part else part
            return (float(m.group(1)), float(m.group(2)), name.strip())
    return None

# code -> {(lat,lon,name): count}
agg = defaultdict(lambda: defaultdict(int))
no_coord = defaultdict(int)
n_files = 0
for dirpath, dirs, files in os.walk(ROOT):
    for f in files:
        if not f.lower().endswith(AUDIO):
            continue
        nm = NAME_RE.match(f)
        if not nm:
            continue
        code = nm.group(1)
        n_files += 1
        c = coord_of_path(dirpath)
        if c is None:
            no_coord[code] += 1
        else:
            agg[code][c] += 1

print(f"scanned audio files with parseable code: {n_files}\n")
print(f"{'CODE':16} {'files':>7}  lat, lon  (folder)  [+ conflicts]")
print("-" * 100)
for code in sorted(agg):
    variants = sorted(agg[code].items(), key=lambda kv: -kv[1])
    (lat, lon, name), cnt = variants[0]
    flag = "  <<< MULTI" if len(variants) > 1 else ""
    print(f"{code:16} {cnt:7d}  {lat:.5f}, {lon:.5f}  ({name}){flag}")
    for (la, lo, nm2), c2 in variants[1:]:
        print(f"{'':16} {c2:7d}  {la:.5f}, {lo:.5f}  ({nm2})")

if no_coord:
    print("\n-- files whose ancestor folders have NO coords --")
    for code, c in sorted(no_coord.items(), key=lambda kv: -kv[1]):
        print(f"  {code:16} {c:7d}")
