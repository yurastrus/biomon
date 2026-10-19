# One-off 2026-10-19: add 5 OBROSHYNE PAM locations (institution 31) via tunnel 5433. Idempotent guard by name. Run: venv/Scripts/python -I scripts/add_obroshyne_locations.py
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','.env'))
from sqlalchemy import create_engine,text
pts=[(1,49.79109,23.88297),(2,49.79467,23.88555),(3,49.79633,23.89176),(4,49.79200,23.89077),(5,49.78825,23.88788)]
e=create_engine(os.environ['PAM_DATABASE_URL'])
with e.begin() as c:
    if c.execute(text("select count(*) from locations where location_name like 'OBROSHYNE-%'")).scalar():
        raise SystemExit('already exists')
    for n,lat,lon in pts:
        name=f'OBROSHYNE-{n}'
        lid=c.execute(text("""INSERT INTO locations (location_name,location_name_en,lat,lon,geom,visibility_level,state_province,created_at)
          VALUES (:n,:n,:lat,:lon,ST_SetSRID(ST_MakePoint(:lon,:lat),4326),0,'Lviv Oblast',NOW()) RETURNING location_id"""),
          dict(n=name,lat=lat,lon=lon)).scalar()
        c.execute(text("INSERT INTO location_institutions (location_id,institution_id) VALUES (:l,31)"),dict(l=lid))
        print(lid,name)
with e.connect() as c:
    for r in c.execute(text("select l.location_id,location_name,lat,lon,st_astext(geom),institution_id from locations l join location_institutions using(location_id) where location_name like 'OBROSHYNE-%' order by 1")): print(tuple(r))
