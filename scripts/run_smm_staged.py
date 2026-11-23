"""Check and load explicitly selected SMM deployments through the normal uploader."""
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

stage = Path(sys.argv[1]).resolve()
apply = sys.argv[2:] == ['--apply']
load_dotenv('/var/www/biomon/.env')
engine = create_engine(os.environ['CT_DATABASE_URL'])
for item in json.loads((stage/'manifest.json').read_text(encoding='utf-8')):
    loc, dep, keys = item['loc'], item['dep'], item['keys']
    offset = item.get('offset',0)
    folder = stage/str(loc)
    assert sum(p.suffix.lower() in ('.jpg','.jpeg') for p in folder.rglob('*')) == len(keys)
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        assert c.execute(text('select location_id from deployments where name=:dep'), {'dep':dep}).scalars().all() == [loc], dep
        if any(c.execute(text(f'select exists(select 1 from {t} where location_id=:l)'), {'l':loc}).scalar_one() for t in ('observations','upload_batches')):
            print('HOLD existing data',dep,flush=True); continue
        hits = c.execute(text('''select distinct k.name,k.ts,p.upload_batch_id from
          jsonb_to_recordset(cast(:keys as jsonb)) as k(name text, ts timestamp)
          join photos p on p.original_filename=k.name and p.captured_at=k.ts at time zone 'UTC' '''), {'keys':json.dumps(keys)}).all()
        if hits:
            print('HOLD matched files',dep,len(hits),hits[:4],flush=True); continue
    print('READY',dep,loc,len(keys),flush=True)
    if not apply:
        continue
    with (stage/f'{loc}.log').open('w') as log:
        result = subprocess.run([sys.executable,'-u',str(stage/'upload.py'),'--location',str(loc),'--user','1','--offset',str(offset),'--apply',str(folder)], stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        print('HOLD uploader error',dep,flush=True); continue
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        stats = c.execute(text('''select b.id,b.status,b.total_files,b.processed_files,count(p.id),count(distinct p.observation_id),
          count(*) filter(where o.location_id is distinct from b.location_id),min(p.captured_at),max(p.captured_at),
          count(*) filter(where k.name is null), b.time_offset_seconds
          from upload_batches b left join photos p on p.upload_batch_id=b.id
          left join observations o on o.id=p.observation_id
          left join jsonb_to_recordset(cast(:keys as jsonb)) as k(name text, ts timestamp)
            on k.name=p.original_filename and p.captured_at=k.ts at time zone 'UTC'
          where b.location_id=:l group by b.id'''),{'l':loc,'keys':json.dumps(keys)}).all()
    if len(stats)==1 and stats[0][1:5]==('completed',len(keys),len(keys),len(keys)) and stats[0][6]==stats[0][9]==0 and stats[0][10]==offset:
        print('VERIFIED',dep,stats[0],flush=True)
    else:
        print('HOLD verify',dep,stats,flush=True)
