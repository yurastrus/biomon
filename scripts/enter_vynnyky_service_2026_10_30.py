"""One-off: Vynnyky service round of Friday 2026-10-30 (NiMH 2500 + 64 GB cards).

Inserts 4 routine visits, fixes the dates of VF1_VERBA (122) and VL4_SVITLYI (123).
Times are approximate (10-12 local, user does not remember); stored the way the
form stores them (local clock time written as UTC). Run once:
    venv/Scripts/python -m scripts.enter_vynnyky_service_2026_10_30
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))
NEW = [(417, '10:10'), (414, '10:30'), (415, '10:50'), (419, '11:10')]
FIX = [(122, '11:30'), (123, '11:50')]

e = create_engine(os.environ['PAM_DATABASE_URL'])
with e.begin() as c:
    exists = c.execute(text("select count(*) from service_visits where location_id = any(:l) and visit_datetime::date = '2026-10-30'"),
                       {'l': [n[0] for n in NEW]}).scalar()
    assert exists == 0, 'already entered'
    for loc, t in NEW:
        c.execute(text("""insert into service_visits (location_id, user_id, visit_datetime, is_operational, battery_type_id,
            sd_card_status_id, recording_hours_per_day, visit_purpose_id, comments, device_type_id)
            values (:l, 1, :dt, null, 1, 3, 24, 1, null, null)"""), {'l': loc, 'dt': f'2026-10-30 {t}+00'})
    for vid, t in FIX:
        c.execute(text("update service_visits set visit_datetime = :dt where id = :i"), {'i': vid, 'dt': f'2026-10-30 {t}+00'})
    for r in c.execute(text("select v.id, l.location_name, v.visit_datetime, v.visit_purpose_id, v.battery_type_id, v.sd_card_status_id from service_visits v join locations l using(location_id) where l.location_id in (414,415,416,417,418,419) and v.visit_datetime >= '2026-10-25' order by 1")):
        print(tuple(r))
