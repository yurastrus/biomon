# One-off 2026-10-19: first (installation) visits of OBROSHYNE-1..5 (locations 409-413), 2026-10-18 ~midday (tunnel 5433).
# Card (64 GB) and batteries (NiMH 2000, lower bound of the 2000-2800 range) are approximate: user did not install them.
# Time stored as typed (app stores datetime-local as-is, DB tz = UTC).
# Run: venv/Scripts/python -I scripts/add_service_visits_obroshyne.py
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
from sqlalchemy import create_engine, text

COMMENT = 'Картка 64 Гб і батарейки (NiMH 2000–2800 mAh) вказані орієнтовно, встановлював не автор запису.'
e = create_engine(os.environ['PAM_DATABASE_URL'])
dt = datetime.fromisoformat('2026-10-18 12:00')
with e.begin() as c:
    for loc in range(409, 414):
        if c.execute(text("select count(*) from service_visits where location_id=:l"), dict(l=loc)).scalar():
            print('skip', loc); continue
        c.execute(text("""INSERT INTO service_visits (location_id,user_id,visit_datetime,is_operational,battery_type_id,
            sd_card_status_id,recording_hours_per_day,visit_purpose_id,comments)
            VALUES (:l,1,:dt,NULL,2,3,6,2,:c)"""), dict(l=loc, dt=dt, c=COMMENT))
        print('added', loc)
with e.connect() as c:
    for r in c.execute(text("select sv.id,l.location_name,sv.visit_datetime,sv.battery_type_id,sv.sd_card_status_id,sv.visit_purpose_id from service_visits sv join locations l using(location_id) where sv.location_id between 409 and 413 order by sv.id")): print(tuple(r))
