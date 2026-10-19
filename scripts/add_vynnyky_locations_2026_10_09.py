# One-off 2026-10-19: 5 Vynnyky project (institution 2, VYNLIS) locations + installation visits of 2026-10-09 (tunnel 5433).
# Times are the local wall clock from the GPS export (+0300), stored as typed (app stores datetime-local as-is, DB tz = UTC).
# NiMH 2500 (id 1), 64 GB (id 3), purpose 2 (installation), 24 h/day (assumed, round-the-clock like OBROSHYNE).
# Run: venv/Scripts/python -I scripts/add_vynnyky_locations_2026_10_09.py
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
from sqlalchemy import create_engine, text

PTS = [('VL2_MIXED', 49.821656, 24.117426, '2026-10-09 10:30'), ('VL3_YAR', 49.827201, 24.115298, '2026-10-09 10:50'),
       ('VL4_SVITLYI', 49.829835, 24.123054, '2026-10-09 11:53'), ('VL1_BEECH', 49.818554, 24.124079, '2026-10-09 10:12'),
       ('VF1_VERBA', 49.829644, 24.139535, '2026-10-09 12:32')]
e = create_engine(os.environ['PAM_DATABASE_URL'])
with e.begin() as c:
    for name, lat, lon, ts in PTS:
        if c.execute(text("select count(*) from locations where location_name=:n"), dict(n=name)).scalar():
            print('skip', name); continue
        lid = c.execute(text("""INSERT INTO locations (location_name,location_name_en,lat,lon,geom,visibility_level,state_province,created_at)
          VALUES (:n,:n,:lat,:lon,ST_SetSRID(ST_MakePoint(:lon,:lat),4326),0,'Lviv Oblast',NOW()) RETURNING location_id"""),
          dict(n=name, lat=lat, lon=lon)).scalar()
        c.execute(text("INSERT INTO location_institutions (location_id,institution_id) VALUES (:l,2)"), dict(l=lid))
        c.execute(text("""INSERT INTO service_visits (location_id,user_id,visit_datetime,is_operational,battery_type_id,
            sd_card_status_id,recording_hours_per_day,visit_purpose_id,comments)
            VALUES (:l,1,:dt,NULL,1,3,24,2,NULL)"""), dict(l=lid, dt=datetime.fromisoformat(ts)))
        print('added', lid, name)
with e.connect() as c:
    for r in c.execute(text("select l.location_id,l.location_name,l.lat,l.lon,li.institution_id,sv.visit_datetime,sv.battery_type_id,sv.sd_card_status_id,sv.recording_hours_per_day,sv.visit_purpose_id from locations l join location_institutions li using(location_id) join service_visits sv using(location_id) where l.location_name ~ '^V[LF][0-9]_' order by 1")): print(tuple(r))
