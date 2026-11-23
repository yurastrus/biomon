from dotenv import load_dotenv
load_dotenv('/var/www/biomon/.env')
import os,json
from sqlalchemy import create_engine,text
e=create_engine(os.environ['CT_DATABASE_URL'])
with e.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    for q in ["select d.name,d.location_id,l.latitude,l.longitude,(select count(*) from observations o where o.location_id=l.id) observations from deployments d join locations l on l.id=d.location_id where d.name like '2024_Summer_Vyzhnytskyi%'", "select b.id,b.location_id,b.status,b.total_files,b.processed_files,count(p.id) photos,count(distinct p.observation_id) series,min(p.captured_at),max(p.captured_at) from upload_batches b left join photos p on p.upload_batch_id=b.id where b.location_id in (select location_id from deployments where name like '2024_Summer_Vyzhnytskyi%') group by b.id", 'select * from ai_control']:
        print(json.dumps([dict(r) for r in c.execute(text(q)).mappings()],default=str))
