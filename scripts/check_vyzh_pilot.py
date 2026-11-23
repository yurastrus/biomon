"""Run on biomon before uploading the staged pilot."""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv('/var/www/biomon/.env')
targets = {3236: ('2205', 264), 3233: ('2207', 301), 3234: ('2208', 267)}
offset = 0
if sys.argv[2:] == ['2204']:
    targets = {3235: ('2204', 282)}
    offset = 4680
engine = create_engine(os.environ['CT_DATABASE_URL'])
with engine.connect() as conn:
    conn.execute(text('SET TRANSACTION READ ONLY'))
    for loc, (cam, n) in targets.items():
        name = f'2024_Summer_Vyzhnytskyi NNP_{cam}'
        assert conn.execute(text('select location_id from deployments where name=:name'), {'name': name}).scalars().all() == [loc]
        for table in ('observations', 'upload_batches'):
            assert not conn.execute(text(f'select exists(select 1 from {table} where location_id=:loc)'), {'loc':loc}).scalar_one(), (loc, table)
        files = list((Path(sys.argv[1])/str(loc)).rglob('*.JPG'))
        assert len(files) == n, (loc,len(files),n)
        found = []
        for path in files:
            with Image.open(path) as im:
                ex = im._getexif() or {}
                ts = datetime.strptime(ex.get(36867) or ex[306], '%Y:%m:%d %H:%M:%S') + timedelta(seconds=offset)
            matches = conn.execute(text('''select o.location_id from photos p join observations o on o.id=p.observation_id
                where p.captured_at=CAST(:ts AS timestamp) AT TIME ZONE 'UTC' and p.original_filename=:name'''),
                {'ts':ts,'name':path.name}).scalars().all()
            if matches:
                found.append((path.name, str(ts), matches))
        print(loc, len(files), 'matching keys:', found, flush=True)
        if any(not match[0].upper().startswith('L') for match in found) or len(found) > 3:
            raise SystemExit('Potential duplicate files: inspect before upload')
