"""User-authorized provisional audio-bound history, dry-run unless --apply."""
import math
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
HIST = [1,2,3,4,14,15,16,17,18,19,20,21,22,23,24,25,26,58,62,313,325,327,329,332]
REMOVE = [404,405,429,430,431,432,433,434,435,436]
TAG = '[provisional-audio-history-2026-11-08]'
apply = '--apply' in sys.argv
if apply:
    raise SystemExit('Disabled: this batch was rolled back at user request. Audio ending does not establish removal.')
with create_engine(os.environ['PAM_DATABASE_URL']).begin() as c:
    if not apply:
        c.execute(text('SET TRANSACTION READ ONLY'))
    rows=c.execute(text('''WITH d AS (
        SELECT location_id,(datetime_start AT TIME ZONE 'Europe/Kyiv')::date dt,
            sum(duration_minutes)/60 hours FROM recordings
        WHERE location_id=ANY(:ids) GROUP BY 1,2)
        SELECT l.location_id,l.location_name,min(r.datetime_start) first_audio,
            max(r.datetime_start) last_audio,count(*) n,
            (SELECT max(hours) FROM d WHERE d.location_id=l.location_id) max_hours
        FROM locations l JOIN recordings r USING(location_id)
        WHERE l.location_id=ANY(:ids) GROUP BY 1,2 ORDER BY 1
    '''),{'ids':HIST+REMOVE}).mappings().all()
    assert len(rows)==34
    inserted=0
    for r in rows:
        loc=r['location_id']
        purposes=[2,3] if loc in HIST else [3]
        for purpose in purposes:
            existing=c.execute(text('SELECT id,comments FROM service_visits WHERE location_id=:l AND visit_purpose_id=:p'),{'l':loc,'p':purpose}).mappings().all()
            if existing:
                assert len(existing)==1 and TAG in (existing[0]['comments'] or ''),(loc,purpose)
                continue
            dt=r['first_audio'] if purpose==2 else r['last_audio']
            prior=c.execute(text('SELECT recording_hours_per_day FROM service_visits WHERE location_id=:l AND visit_purpose_id=2 ORDER BY visit_datetime DESC LIMIT 1'),{'l':loc}).scalar()
            hours=prior or min(24,max(1,math.ceil(r['max_hours'] or 0)))
            battery=1 if purpose==2 and r['location_name'].startswith('Розточчя') else None
            comment=('Умовний сервісний запис, відновлений постфактум на основі наявних аудіоданих за вказівкою користувача. '
                + ('Встановлення датовано початком першого наявного аудіофайла; фактична дата монтажу не підтверджена. ' if purpose==2 else
                   'Демонтаж умовно датовано початком останнього аудіофайла. Аудіодані не дозволяють відрізнити демонтаж від сервісного забору картки або зупинки запису; факт і дата демонтажу не підтверджені. ')
                + f'У базі {r["n"]} аудіофайлів. Модель приладу та місткість SD невідомі; SD «залишено наявну» — технічна заглушка. '
                + (f'Години ({hours}) перенесено з наявного встановлення. ' if prior else
                   f'Години ({hours}) — округлена вгору максимальна наявна добова тривалість, нижня оцінка, а не підтверджений розклад. ')
                + ('Батареї: чорні NiMH 2500 мАг — прийняте користувачем припущення для відновлених встановлень Розточчя, не визначене з аудіо. ' if battery else '')
                + ('Дуже мало файлів; стаціонарний деплоймент не підтверджений. ' if r['n']<=6 else '')
                + ('Перший час 01.07.2024 00:00 UTC однаковий у кількох точок; можливий артефакт старого імпорту. ' if loc in [1,2,3,4] else '')
                + TAG)
            print(loc,purpose,dt,'hours',hours,'battery',battery)
            if apply:
                c.execute(text('''INSERT INTO service_visits
                    (location_id,user_id,visit_datetime,visit_purpose_id,battery_type_id,
                     sd_card_status_id,recording_hours_per_day,comments)
                    VALUES(:l,1,:dt,:p,:b,1,:h,:comment)'''),dict(l=loc,dt=dt,p=purpose,b=battery,h=hours,comment=comment))
            inserted+=1
    print('APPLIED' if apply else 'DRY RUN',inserted)
