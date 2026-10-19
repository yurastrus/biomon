# One-off 2026-10-19: service visits of 2026-07-21 for locations 121, 389, 400, 401, 399 (tunnel 5433).
# Times stored as typed (app stores datetime-local as-is, DB tz = UTC).
# Run: venv/Scripts/python -I scripts/add_service_visits_2026_07_21.py
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
from sqlalchemy import create_engine, text

# location_id, time, battery_type_id (1 = NiMH 2500, 2 = NiMH 2000), sd_card_status_id (3 = 64 GB)
VISITS = [(121, '2026-07-21 12:45', 1, 3), (389, '2026-07-21 13:16', 1, 3), (400, '2026-07-21 13:34', 1, 3),
          (401, '2026-07-21 13:56', 1, 3), (399, '2026-07-21 14:21', 2, 3)]
e = create_engine(os.environ['PAM_DATABASE_URL'])
with e.begin() as c:
    for loc, ts, bat, sd in VISITS:
        dt = datetime.fromisoformat(ts)
        if c.execute(text("select count(*) from service_visits where location_id=:l and visit_datetime::date=:d"), dict(l=loc, d=dt.date())).scalar():
            print('skip', loc); continue
        c.execute(text("""INSERT INTO service_visits (location_id,user_id,visit_datetime,is_operational,battery_type_id,
            sd_card_status_id,recording_hours_per_day,visit_purpose_id,comments)
            VALUES (:l,1,:dt,NULL,:b,:s,6,1,NULL)"""), dict(l=loc, dt=dt, b=bat, s=sd))
        print('added', loc, ts)
with e.connect() as c:
    for r in c.execute(text("select sv.id,sv.location_id,l.location_name,sv.visit_datetime,sv.battery_type_id,sv.sd_card_status_id,sv.visit_purpose_id from service_visits sv join locations l using(location_id) where sv.visit_datetime::date='2026-07-21' order by sv.id")): print(tuple(r))
