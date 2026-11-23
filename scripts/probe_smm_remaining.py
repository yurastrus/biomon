import os
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv('/var/www/biomon/.env')
with create_engine(os.environ['CT_DATABASE_URL']).connect() as conn:
    conn.execute(text('SET TRANSACTION READ ONLY'))
    rows = conn.execute(text('''select d.name,d.location_id,d.start_date,d.end_date,
        l.latitude,l.longitude,(select count(*) from observations o where o.location_id=l.id) obs,
        (select count(*) from upload_batches b where b.location_id=l.id) batches
        from deployments d left join locations l on l.id=d.location_id
        where d.name ~ '^2024_Summer_(Karpatskyi NNP|Synevyr NNP|Verkhovynskyi NNP)_' '''))
    print(json.dumps([dict(row) for row in rows.mappings()],default=str,ensure_ascii=True))
