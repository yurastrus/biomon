"""
Field battery life of Song Meter Micro recorders from their *_Summary.txt logs.

Each deployment folder holds a log with one line per minute:
DATE,TIME,LAT,NS,LON,EW,POWER(V),#FILES. #FILES=1 marks the minute a WAV file
was started, so recorded hours = files x file length (read from a WAV header in
the same folder). Battery chemistry is told by the starting voltage
(NiMH 5.0-6.0 V, lithium >= 6.6 V); a log ending at <= 4.1 V is a run that
ended because the batteries were exhausted.

Prints exhausted-run hours per chemistry and season, plus a voltage -> hours
left table (how much a battery that reads V still has), and writes one CSV row
per deployment.

Read-only. Run:
    venv/Scripts/python -m scripts.analyze_songmeter_battery "F:/1_Acoustic_recordings/1_Hearable_recordings"
"""

import csv
import glob
import os
import statistics as st
import sys
import wave
from collections import Counter
from datetime import datetime

DEAD_V = 4.1


def chemistry(v0):
    if 5.0 <= v0 <= 6.0:
        return 'NiMH'
    return 'Li' if v0 >= 6.6 else '?'


def season(month):
    if month in (11, 12, 1, 2):
        return 'winter'
    if month in (3, 4, 5):
        return 'spring'
    return 'summer' if month in (6, 7, 8) else 'autumn'


def read_log(path):
    pts = []
    with open(path, encoding='utf-8', errors='replace') as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for x in reader:
            try:
                pts.append((datetime.strptime(x[0] + ' ' + x[1], '%Y-%b-%d %H:%M:%S'),
                            float(x[6]), int(x[7])))
            except (ValueError, IndexError):
                pass
    return pts


def file_length(folder):
    wavs = sorted(glob.glob(os.path.join(folder, '*.wav')))
    for w in wavs[1:4] or wavs[:1]:     # the first file of a run is often short
        try:
            with wave.open(w) as ww:
                return ww.getnframes() / ww.getframerate(), ww.getframerate()
        except (wave.Error, OSError, EOFError):
            continue
    return None, None


def main(root, out_csv):
    runs, left_by_v = [], {}
    for path in glob.glob(os.path.join(root, '**', '*_Summary.txt'), recursive=True):
        pts = read_log(path)
        if len(pts) < 60:
            continue
        dur, sr = file_length(os.path.dirname(path))
        if not dur:
            continue
        days = (pts[-1][0] - pts[0][0]).total_seconds() / 86400
        nfiles = sum(p[2] for p in pts)
        rec_h = nfiles * dur / 3600
        chem = chemistry(pts[0][1])
        dead = pts[-1][1] <= DEAD_V
        runs.append(dict(path=os.path.relpath(path, root), start=pts[0][0].date(), days=round(days, 1),
                         chem=chem, v_start=pts[0][1], v_end=pts[-1][1], exhausted=dead,
                         files=nfiles, file_s=round(dur), sample_rate=sr, rec_h=round(rec_h, 1),
                         h_per_day=round(rec_h / days, 1) if days else None,
                         season=season(pts[0][0].month)))
        if dead and days > 2 and chem != '?':
            done, seen = 0, set()
            for _, v, n in pts:
                done += n
                if v not in seen:
                    seen.add(v)
                    left_by_v.setdefault((chem, v), []).append((nfiles - done) * dur / 3600)

    with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(runs[0]))
        writer.writeheader()
        writer.writerows(runs)
    print(f'{len(runs)} deployments -> {out_csv}')
    print('sample rates', dict(Counter(r['sample_rate'] for r in runs)))

    for chem in ('NiMH', 'Li'):
        dead = [r for r in runs if r['chem'] == chem and r['exhausted'] and r['days'] > 2]
        if len(dead) < 2:
            continue
        hours = [r['rec_h'] for r in dead]
        q = st.quantiles(hours, n=4)
        print(f'\n{chem}: {len(dead)} exhausted runs, recorded hours median {st.median(hours):.0f} '
              f'(IQR {q[0]:.0f}-{q[2]:.0f}); runs not exhausted: '
              f'{sum(1 for r in runs if r["chem"] == chem and not r["exhausted"])}')
        for s in ('winter', 'spring', 'summer', 'autumn'):
            v = [r['rec_h'] for r in dead if r['season'] == s]
            if v:
                print(f'  {s:7s} n={len(v):3d}  median {st.median(v):.0f} h')
        print('  voltage -> recorded hours left (median, q10-q90)')
        for (c, v), left in sorted(left_by_v.items(), reverse=True):
            if c == chem and len(left) >= 3:
                d = st.quantiles(left, n=10)
                print(f'    {v:.1f} V  n={len(left):3d}  {st.median(left):5.0f}  ({d[0]:.0f}-{d[-1]:.0f})')


if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else r'F:\1_Acoustic_recordings\1_Hearable_recordings'
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    main(root, os.path.join('logs', f'songmeter_battery_runs_{stamp}.csv'))
