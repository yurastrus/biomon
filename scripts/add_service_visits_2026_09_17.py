# One-off 2026-10-19: service visits of 2026-09-17 for locations 9, 393, 394 (tunnel 5433).
# Times are stored as typed (the app stores the datetime-local value as-is, DB tz = UTC).
# Run: venv/Scripts/python -I scripts/add_service_visits_2026_09_17.py
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
from sqlalchemy import create_engine, text

# location_id, time, battery_type_id (1 = NiMH 2500, 4 = lithium AA), sd_card_status_id (3 = 64 GB, 5 = 256 GB)
VISITS = [(9, '2026-09-17 12:36', 1, 3), (393, '2026-09-17 12:55', 4, 3), (394, '2026-09-17 13:32', 4, 5)]
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
    for r in c.execute(text("select id,location_id,visit_datetime,battery_type_id,sd_card_status_id,recording_hours_per_day,visit_purpose_id from service_visits where visit_datetime::date='2026-09-17' order by id")): print(tuple(r))
