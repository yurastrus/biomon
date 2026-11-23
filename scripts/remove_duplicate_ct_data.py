# SPDX-License-Identifier: AGPL-3.0-only
"""
Remove photo sets that were uploaded to biomon twice (decided 2026-11-05).

Group 1 - the same card uploaded twice into two locations of the SAME
camera-season. Keep the location whose name matches the Excel deployment_id,
delete every photo / series / identification / AI prediction / batch of the
copy, re-point the copy's deployment to the keeper, log the merge and delete
the copy location.

Group 2 - photos from ANOTHER season left on the card and uploaded again with
the next deployment. The same photos already sit in their own season's
location. In the wrong location we delete the series lying entirely outside
that location's deployment interval (+-1 day), i.e. before its start or
after its end. No series straddles the boundary (checked before writing).

Shared - one camera's card uploaded into another camera's location too:
delete the series made only of photos that also sit in the owner location.

Also merges empty duplicate locations (no photos on the duplicate side).
Groups 1 and empty were applied 2026-11-05; rerun only new steps with --only.

    venv/Scripts/python -m scripts.remove_duplicate_ct_data            # dry run
    venv/Scripts/python -m scripts.remove_duplicate_ct_data --apply
Writes logs/remove_duplicate_ct_data_<stamp>.json with every deleted id and
the system_filenames whose thumbnail/raw files must be removed on the server.
Reads CT_DATABASE_URL from .env (locally = PROD through the tunnel).
"""
import argparse
import json
import os
from datetime import datetime

import sqlalchemy as sa
from dotenv import load_dotenv

# (copy location to delete entirely, keeper location)
GROUP1 = [(1276, 39), (1275, 41)]
# locations holding another season's photos (series outside their own interval)
GROUP2 = [1442, 37, 36, 1504, 118, 117, 1492]
# (location holding copies, owner location): delete series in the first that
# consist ONLY of photos also present (same file + time) in the owner.
# 105 Cheremoskyi 2023 Summer 1606 holds camera 1602's card (stamp "1602").
SHARED = [(105, 104)]
# empty duplicates (dup -> keeper); the dup side must have 0 series
EMPTY_MERGE = [(1274, 40), (1140, 3138), (1141, 3139), (1142, 3140), (1143, 166),
               (1144, 3141), (1150, 3144), (1159, 3153), (1160, 167)]


def obs_ids_group2(c, loc):
    return [r[0] for r in c.execute(sa.text("""
        with dp as (select min(start_date) s, max(end_date) e from deployments where location_id = :l)
        select o.id from observations o, dp
         where o.location_id = :l
           and (o.series_end_time::date < dp.s - 1 or o.series_start_time::date > dp.e + 1)
    """), {'l': loc})]


def obs_ids_shared(c, loc, owner):
    return [r[0] for r in c.execute(sa.text("""
        select o.id from observations o where o.location_id = :l
           and not exists (
               select 1 from photos p where p.observation_id = o.id
                  and not exists (select 1 from photos q join observations oq on oq.id = q.observation_id
                                   where oq.location_id = :w and q.original_filename = p.original_filename
                                     and q.captured_at = p.captured_at))
    """), {'l': loc, 'w': owner})]


def delete_observations(c, obs):
    if not obs:
        return {}, []
    files = [r[0] for r in c.execute(sa.text(
        "select system_filename from photos where observation_id = any(:o)"), {'o': obs})]
    n = {}
    n['ai'] = c.execute(sa.text("""delete from ai_predictions where observation_id = any(:o)
        or photo_id in (select id from photos where observation_id = any(:o))"""), {'o': obs}).rowcount
    n['behaviors'] = c.execute(sa.text("""delete from identification_behaviors where identification_id in (
        select i.id from identifications i join photos p on p.id = i.photo_id
         where p.observation_id = any(:o))"""), {'o': obs}).rowcount
    n['identifications'] = c.execute(sa.text("""delete from identifications
        where photo_id in (select id from photos where observation_id = any(:o))"""), {'o': obs}).rowcount
    n['photos'] = c.execute(sa.text("delete from photos where observation_id = any(:o)"), {'o': obs}).rowcount
    n['observations'] = c.execute(sa.text("delete from observations where id = any(:o)"), {'o': obs}).rowcount
    return n, files


def recount(c, loc):
    c.execute(sa.text("""update locations set photo_count = (
        select count(*) from photos p join observations o on o.id = p.observation_id
         where o.location_id = :l and p.status in ('pending','completed','needs_review'))
        where id = :l"""), {'l': loc})


def merge_location(c, dup, keep, user, why):
    name = c.execute(sa.text("select name from locations where id = :d"), {'d': dup}).scalar()
    for t in ('observations', 'upload_batches', 'deployments', 'service_visits'):
        c.execute(sa.text(f"update {t} set location_id = :k where location_id = :d"), {'k': keep, 'd': dup})
    for t, col in (('location_institutions', 'institution_id'), ('location_biotopes', 'biotope_id')):
        c.execute(sa.text(f"""insert into {t} (location_id, {col})
            select :k, {col} from {t} where location_id = :d on conflict do nothing"""), {'k': keep, 'd': dup})
    c.execute(sa.text("""insert into location_merge_log
        (merged_by_id, main_location_id, merged_location_ids, merged_location_names, merge_reason, created_at)
        values (:u, :k, :ids, :names, :why, now())"""),
        {'u': user, 'k': keep, 'ids': [dup], 'names': [name], 'why': why})
    c.execute(sa.text("delete from locations where id = :d"), {'d': dup})
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--user-id', type=int, default=1)
    ap.add_argument('--only', nargs='*', choices=['group1', 'group2', 'shared', 'empty'],
                    help='run only these steps (default: all; group1/empty are one-shot)')
    args = ap.parse_args()
    steps = set(args.only or ['group1', 'group2', 'shared', 'empty'])
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
    engine = sa.create_engine(os.environ['CT_DATABASE_URL'].strip("'\""))
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    report = {'group1': [], 'group2': [], 'shared': [], 'empty_merge': [], 'files': []}

    conn = engine.connect()
    tx = conn.begin()
    c = conn
    try:
        c.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
        for dup, keep in (GROUP1 if 'group1' in steps else []):
            obs = [r[0] for r in c.execute(sa.text(
                "select id from observations where location_id = :l"), {'l': dup})]
            n, files = delete_observations(c, obs)
            n['batches'] = c.execute(sa.text("""delete from upload_batches b where location_id = :l
                and not exists (select 1 from photos p where p.upload_batch_id = b.id)"""), {'l': dup}).rowcount
            name = merge_location(c, dup, keep, args.user_id,
                                  'Same card uploaded twice; copy deleted (scripts.remove_duplicate_ct_data)')
            recount(c, keep)
            report['group1'].append({'dup': dup, 'name': name, 'keep': keep, 'obs': obs, **n})
            report['files'] += files
            print(f'G1 {dup} {name} -> keep {keep}: {n}')
        for loc in (GROUP2 if 'group2' in steps else []):
            obs = obs_ids_group2(c, loc)
            n, files = delete_observations(c, obs)
            recount(c, loc)
            report['group2'].append({'loc': loc, 'obs': obs, **n})
            report['files'] += files
            print(f'G2 {loc}: {n}')
        for loc, owner in (SHARED if 'shared' in steps else []):
            obs = obs_ids_shared(c, loc, owner)
            n, files = delete_observations(c, obs)
            recount(c, loc)
            report['shared'].append({'loc': loc, 'owner': owner, 'obs': obs, **n})
            report['files'] += files
            print(f'SHARED {loc} (copies of {owner}): {n}')
        for dup, keep in (EMPTY_MERGE if 'empty' in steps else []):
            have = c.execute(sa.text("select count(*) from observations where location_id = :a"),
                             {'a': dup}).scalar()
            if have:
                print(f'EMPTY {dup}->{keep}: SKIP, {have} series present')
                continue
            name = merge_location(c, dup, keep, args.user_id,
                                  'Empty duplicate location (scripts.remove_duplicate_ct_data)')
            report['empty_merge'].append({'dup': dup, 'name': name, 'keep': keep})
            print(f'EMPTY {dup} {name} -> {keep}')
        print(f"files to remove: {len(report['files'])}")
        if args.apply:
            tx.commit()
            os.makedirs('logs', exist_ok=True)
            path = f'logs/remove_duplicate_ct_data_{stamp}.json'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            print('APPLIED. Report:', path)
        else:
            tx.rollback()
            print('DRY RUN (rolled back). Add --apply.')
    except Exception:
        tx.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
