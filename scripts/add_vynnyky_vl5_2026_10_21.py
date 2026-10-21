# One-off 2026-10-21: VL5_SOSNSHYL (Vynnyky project, institution 2) + installation visit of 2026-10-21 12:00 (tunnel 5433).
# NiMH 2500 (id 1), 64 GB (id 3), purpose 2 (installation), 24 h/day. Time stored as typed (DB tz = UTC).
# Run: venv/Scripts/python -I scripts/add_vynnyky_vl5_2026_10_21.py
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
from sqlalchemy import create_engine, text

NAME, LAT, LON = 'VL5_SOSNSHYL', 49.82427, 24.12048
e = create_engine(os.environ['PAM_DATABASE_URL'])
with e.begin() as c:
    if c.execute(text("select count(*) from locations where location_name=:n"), dict(n=NAME)).scalar():
        raise SystemExit('already exists')
    lid = c.execute(text("""INSERT INTO locations (location_name,location_name_en,lat,lon,geom,visibility_level,state_province,created_at)
      VALUES (:n,:n,:lat,:lon,ST_SetSRID(ST_MakePoint(:lon,:lat),4326),0,'Lviv Oblast',NOW()) RETURNING location_id"""),
      dict(n=NAME, lat=LAT, lon=LON)).scalar()
    c.execute(text("INSERT INTO location_institutions (location_id,institution_id) VALUES (:l,2)"), dict(l=lid))
    c.execute(text("""INSERT INTO service_visits (location_id,user_id,visit_datetime,is_operational,battery_type_id,
        sd_card_status_id,recording_hours_per_day,visit_purpose_id,comments)
        VALUES (:l,1,:dt,NULL,1,3,24,2,NULL)"""), dict(l=lid, dt=datetime.fromisoformat('2026-10-21 12:00')))
    print('added', lid, NAME)
with e.connect() as c:
    for r in c.execute(text("select l.location_id,l.location_name,l.lat,l.lon,li.institution_id,sv.visit_datetime,sv.battery_type_id,sv.sd_card_status_id,sv.recording_hours_per_day,sv.visit_purpose_id from locations l join location_institutions li using(location_id) join service_visits sv using(location_id) where l.location_name='VL5_SOSNSHYL'")): print(tuple(r))
