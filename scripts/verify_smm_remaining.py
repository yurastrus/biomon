"""Read-only independent audit of the two SMM upload groups."""
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv('/var/www/biomon/.env')
expected = {3197:62,3200:189,3209:226,3207:494,3198:140,3199:323,3192:173,3201:246,3202:273,3206:245,3203:123,
            3204:178,3205:486,1277:309,1213:71,3228:27,3230:141,3231:90}
offsets = {}
if len(sys.argv)>1:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    expected = {r['loc']:len(r['keys']) for r in manifest}
    offsets = {r['loc']:r.get('offset',0) for r in manifest}
with create_engine(os.environ['CT_DATABASE_URL']).connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    rows = c.execute(text('''select d.name, b.location_id, b.id, b.status, b.total_files,
        b.processed_files, b.time_offset_seconds, count(p.id) photos, count(distinct p.observation_id) series,
        count(*) filter(where o.location_id is distinct from b.location_id) invalid_links,
        min(p.captured_at) first, max(p.captured_at) last
        from upload_batches b join deployments d on d.location_id=b.location_id and d.name ~ '^2024_Summer_(Karpatskyi NNP|Synevyr NNP|Verkhovynskyi NNP)_'
        left join photos p on p.upload_batch_id=b.id left join observations o on o.id=p.observation_id
        where b.location_id=any(:ids) group by d.name,b.id order by d.name'''),{'ids':list(expected)}).mappings().all()
    duplicates = c.execute(text('''select distinct b.location_id from photos p
        join upload_batches b on b.id=p.upload_batch_id join photos q
        on q.original_filename=p.original_filename and q.captured_at=p.captured_at and q.id<>p.id
        where b.location_id=any(:ids)'''),{'ids':list(expected)}).scalars().all()
    for row in rows:
        r = dict(row); n = expected[r['location_id']]
        r['verified'] = (r['status']=='completed' and r['total_files']==r['processed_files']==r['photos']==n
                         and r['time_offset_seconds']==offsets.get(r['location_id'],0) and r['invalid_links']==0 and r['location_id'] not in duplicates
                         and sum(x['location_id']==r['location_id'] for x in rows)==1)
        print(json.dumps(r,default=str,ensure_ascii=True))
    print(json.dumps({'missing':sorted(set(expected)-{r['location_id'] for r in rows}),
                      'duplicates':duplicates,
                      'ai_pause':[dict(r) for r in c.execute(text('select pause_until,pause_reason from ai_control')).mappings()]},default=str))
