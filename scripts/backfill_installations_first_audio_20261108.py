"""Reconstruct ten requested installation visits. Dry run unless --apply."""
import math
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
IDS = [404,405,429,430,431,432,433,434,435,436]
TAG = '[first-audio-install-2026-11-08]'
apply = '--apply' in sys.argv
with create_engine(os.environ['PAM_DATABASE_URL']).begin() as c:
    if not apply:
        c.execute(text('SET TRANSACTION READ ONLY'))
    rows = c.execute(text('''WITH daily AS (
        SELECT location_id,(datetime_start AT TIME ZONE 'Europe/Kyiv')::date d,
               sum(duration_minutes)/60 hours FROM recordings
        WHERE location_id=ANY(:ids) GROUP BY 1,2)
        SELECT location_id,min(datetime_start) first_audio,
            (SELECT max(hours) FROM daily d WHERE d.location_id=r.location_id) max_hours
        FROM recordings r WHERE location_id=ANY(:ids) GROUP BY 1 ORDER BY 1
    '''), {'ids':IDS}).mappings().all()
    assert len(rows)==10
    for r in rows:
        existing=c.execute(text('SELECT id,comments FROM service_visits WHERE location_id=:l'),
                           {'l':r['location_id']}).mappings().all()
        if existing:
            assert len(existing)==1 and TAG in (existing[0]['comments'] or ''), r
            print('Already entered',r['location_id'])
            continue
        hours=min(24,max(1,math.ceil(r['max_hours'] or 0)))
        comment=('Встановлення відновлено постфактум на основі даних про аудіофайли '
            'за вказівкою користувача: дата й час дорівнюють початку першого аудіофайла. '
            'Фактичний час встановлення не підтверджений. '
            'Модель приладу, батареї та місткість SD невідомі. '
            'SD «залишено наявну» — технічна заглушка обов’язкового поля. '
            f'Години запису ({hours}) — округлена вгору максимальна наявна добова '
            'тривалість аудіо, нижня оцінка, а не підтверджений розклад. '+TAG)
        p=dict(l=r['location_id'],dt=r['first_audio'],h=hours,comment=comment)
        print(r['location_id'],r['first_audio'],'hours estimate',hours)
        if apply:
            c.execute(text('''INSERT INTO service_visits
                (location_id,user_id,visit_datetime,visit_purpose_id,battery_type_id,
                 sd_card_status_id,recording_hours_per_day,comments,device_type_id,is_operational)
                VALUES(:l,1,:dt,2,NULL,1,:h,:comment,NULL,NULL)'''),p)
    print('APPLIED' if apply else 'DRY RUN')
