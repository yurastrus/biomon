# SPDX-License-Identifier: AGPL-3.0-only
"""
Merge duplicate CT locations created twice from the ARD Excel (2026-11-05).

The deployment import ran once from the legacy sheet "Data 2023-2024" (old
park spellings: Uzhanski, Zacharovany Kraii, Carpathian Biosphere Reserve...)
and once from the per-campaign sheets (SMM_2024: Uzhanskyi, Zacharovanyi
Krai, Karpatskyi BR...). Each import created its own location at the same
point, so every such camera-season exists twice.

Pair rule: same campaign prefix (e.g. 2024_Summer) + same camera id + points
closer than MAX_DIST_M. Keeper = the location whose deployment name is listed
in a per-campaign sheet (not "Data 2023-2024"); that is the one the current
deployments table refers to. The duplicate's observations, upload batches,
deployments, service visits, institutions and biotopes move to the keeper,
a location_merge_log row is written, and the duplicate is deleted.

Deployments are never deleted: the legacy deployment row is re-pointed to the
keeper, so no deployment loses its location. Re-running the importer without
--create-locations then binds legacy names to the keeper by coordinates.

    venv/Scripts/python -m scripts.merge_duplicate_ard_locations            # dry run
    venv/Scripts/python -m scripts.merge_duplicate_ard_locations --apply
Options: --file <xlsx> (default: Downloads ARD export), --user-id <main-DB id>.
Reads CT_DATABASE_URL from .env (locally that is PROD through the tunnel).
"""
import argparse
import json
import math
import os
import re
import warnings
from collections import defaultdict
from datetime import datetime

import openpyxl
import sqlalchemy as sa
from dotenv import load_dotenv

LEGACY_SHEET = 'Data 2023-2024'
MAX_DIST_M = 5
NAME_RE = re.compile(r'^(\d{4}(?:-\d{4})?_(?:Summer|Winter))_(.+)_([^_]+)$')


def excel_sheet_of(path):
    """{deployment_id: set(sheet names)} over every sheet that has the column."""
    warnings.filterwarnings('ignore')
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = defaultdict(set)
    for s in wb.sheetnames:
        rows = wb[s].iter_rows(values_only=True)
        header = next(rows, None)
        if not header or 'deployment_id' not in header:
            continue
        i = header.index('deployment_id')
        for r in rows:
            if r[i]:
                out[_unpad(str(r[i]).strip())].add(s)
    return out


def _unpad(name):
    """Excel keys use the zero-padded camera id ('..._0310'); the import stored
    some deployments without it ('..._310'). Compare on the unpadded form."""
    head, _, cam = name.rpartition('_')
    return f'{head}_{cam.lstrip("0") or cam}' if head else name


def _campaign(prefix):
    """The legacy sheet named winter campaigns by their start year only
    ('2023_Winter'); per-campaign sheets use '2023-2024_Winter'. Same campaign."""
    m = re.match(r'^(\d{4})_Winter$', prefix)
    return f'{m.group(1)}-{int(m.group(1)) + 1}_Winter' if m else prefix


def dist_m(a, b):
    dlat = (float(a['latitude']) - float(b['latitude'])) * 111_320
    dlon = (float(a['longitude']) - float(b['longitude'])) * 111_320 * math.cos(math.radians(float(a['latitude'])))
    return math.hypot(dlat, dlon)


def find_pairs(c, sheets_of):
    locs = {r['id']: dict(r) for r in c.execute(sa.text(
        "select id, name, latitude, longitude, visibility_level, is_valid, description from locations")).mappings()}
    deps = defaultdict(list)
    for r in c.execute(sa.text("select id, name, location_id from deployments where location_id is not null")).mappings():
        deps[r['location_id']].append(r['name'])
    groups = defaultdict(list)
    for l in locs.values():
        m = NAME_RE.match(l['name'].strip())
        if m:
            groups[(_campaign(m.group(1)), m.group(3).lstrip('0'))].append(l)

    def is_current(loc):
        names = deps.get(loc['id']) or [loc['name']]
        return any(sheets_of.get(_unpad(n), set()) - {LEGACY_SHEET} for n in names)

    nobs = defaultdict(int)
    for lid, n in c.execute(sa.text("select location_id, count(*) from observations group by 1")):
        nobs[lid] = n

    pairs, problems = [], []
    for key, ls in groups.items():
        for i in range(len(ls)):
            for j in range(i + 1, len(ls)):
                a, b = ls[i], ls[j]
                if dist_m(a, b) > MAX_DIST_M:
                    continue
                ca, cb = is_current(a), is_current(b)
                if ca == cb:
                    problems.append((a['id'], a['name'], b['id'], b['name'], 'both current' if ca else 'neither current'))
                    continue
                keep, dup = (a, b) if ca else (b, a)
                if nobs[keep['id']] and nobs[dup['id']]:
                    # Both hold photos: likely the same card uploaded twice.
                    # Merging would double every count, so leave it to a human.
                    problems.append((dup['id'], dup['name'], keep['id'], keep['name'],
                                     f"both have data ({nobs[dup['id']]} / {nobs[keep['id']]} obs)"))
                    continue
                pairs.append((keep, dup))
    return pairs, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--file', default=os.path.expanduser('~/Downloads/CT_LocationARD_Dataset_2026-10-23_11-27.xlsx'))
    ap.add_argument('--user-id', type=int, default=1)
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
    engine = sa.create_engine(os.environ['CT_DATABASE_URL'].strip("'\""))
    sheets_of = excel_sheet_of(args.file)
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    backup = {'pairs': []}

    with engine.begin() as c:
        c.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
        pairs, problems = find_pairs(c, sheets_of)
        for p in problems:
            print('SKIP (ambiguous keeper):', p)
        tot = defaultdict(int)
        for keep, dup in sorted(pairs, key=lambda p: p[0]['name']):
            k, d = keep['id'], dup['id']
            snap = {'keep': [k, keep['name']], 'dup': [d, dup['name']]}
            for t in ('observations', 'upload_batches', 'deployments', 'service_visits'):
                snap[t] = [r[0] for r in c.execute(sa.text(f"select id from {t} where location_id = :d"), {'d': d})]
            snap['institutions'] = [r[0] for r in c.execute(sa.text(
                "select institution_id from location_institutions where location_id = :d"), {'d': d})]
            snap['biotopes'] = [r[0] for r in c.execute(sa.text(
                "select biotope_id from location_biotopes where location_id = :d"), {'d': d})]
            snap['location_row'] = {kk: str(v) for kk, v in dup.items()}
            backup['pairs'].append(snap)
            for t in ('observations', 'upload_batches', 'deployments', 'service_visits'):
                tot[t] += len(snap[t])
            print(f"{d:5} {dup['name']:48} -> {k:5} {keep['name']:42} "
                  f"obs={len(snap['observations'])} bat={len(snap['upload_batches'])} "
                  f"dep={len(snap['deployments'])} sv={len(snap['service_visits'])} "
                  f"dist={dist_m(keep, dup):.1f}m")

            if not args.apply:
                continue
            for t in ('observations', 'upload_batches', 'deployments', 'service_visits'):
                c.execute(sa.text(f"update {t} set location_id = :k where location_id = :d"), {'k': k, 'd': d})
            c.execute(sa.text("""insert into location_institutions (location_id, institution_id)
                select :k, institution_id from location_institutions where location_id = :d
                on conflict do nothing"""), {'k': k, 'd': d})
            c.execute(sa.text("""insert into location_biotopes (location_id, biotope_id)
                select :k, biotope_id from location_biotopes where location_id = :d
                on conflict do nothing"""), {'k': k, 'd': d})
            c.execute(sa.text("""insert into location_merge_log
                (merged_by_id, main_location_id, merged_location_ids, merged_location_names, merge_reason, created_at)
                values (:u, :k, :ids, :names, :why, now())"""),
                {'u': args.user_id, 'k': k, 'ids': [d], 'names': [dup['name']],
                 'why': f'Duplicate from ARD sheet "{LEGACY_SHEET}" (legacy park spelling); '
                        f'scripts.merge_duplicate_ard_locations {stamp}'})
            c.execute(sa.text("delete from locations where id = :d"), {'d': d})
            c.execute(sa.text("""update locations set photo_count = (
                select count(*) from photos p join observations o on o.id = p.observation_id
                 where o.location_id = :k and p.status in ('pending','completed','needs_review'))
                where id = :k"""), {'k': k})

        print(f"\n{len(pairs)} pairs, {len(problems)} skipped; moving " + ', '.join(f'{t}={n}' for t, n in tot.items()))
        if args.apply:
            os.makedirs('logs', exist_ok=True)
            path = f'logs/merge_duplicate_ard_locations_backup_{stamp}.json'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(backup, f, ensure_ascii=False, indent=1)
            print('APPLIED. Backup of moved ids:', path)
        else:
            print('DRY RUN, nothing written. Add --apply.')


if __name__ == '__main__':
    main()
