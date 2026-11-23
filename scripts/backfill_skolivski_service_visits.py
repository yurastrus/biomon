"""Reconstruct 19 Skolivski deployments from audio bounds; --apply writes.

Daily hours are rounded-up observed maximum daily duration, a lower bound
on the intended schedule, not an assertion of recorder configuration.
"""
import json
import math
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
apply = '--apply' in sys.argv
tag = '[skolivski-audio-backfill-2026-11-08]'
with create_engine(os.environ['PAM_DATABASE_URL']).begin() as c:
    if not apply:
        c.execute(text('SET TRANSACTION READ ONLY'))
    rows = c.execute(text('''WITH daily AS (
        SELECT r.location_id,(r.datetime_start AT TIME ZONE 'Europe/Kyiv')::date d,
               sum(r.duration_minutes)/60 hours
        FROM recordings r WHERE EXISTS (SELECT 1 FROM location_institutions li
            WHERE li.location_id=r.location_id AND li.institution_id=6)
        GROUP BY 1,2
    ), bounds AS (
        SELECT r.location_id,min(datetime_start) first_audio,max(datetime_start) last_audio,
               count(*) n FROM recordings r WHERE EXISTS (
               SELECT 1 FROM location_institutions li
               WHERE li.location_id=r.location_id AND li.institution_id=6)
        GROUP BY 1
    ) SELECT b.*,l.location_name,(SELECT max(hours) FROM daily d
        WHERE d.location_id=b.location_id) max_hours
        FROM bounds b JOIN locations l USING(location_id) ORDER BY b.location_id
    ''')).mappings().all()
    assert len(rows) == 19
    for r in rows:
        existing = c.execute(text('SELECT id,comments FROM service_visits WHERE location_id=:l'),
                             {'l':r['location_id']}).mappings().all()
        if existing:
            assert len(existing) == 2 and all(tag in (v['comments'] or '') for v in existing), r
            print('Already reconstructed:', r['location_id'])
            continue
        hours = min(24, max(1, math.ceil(r['max_hours'] or 0)))
        for purpose,dt in [(2,r['first_audio']),(3,r['last_audio'])]:
            comment = ('Запис відновлено постфактум на основі даних про аудіофайли. '
                + ('Встановлення датовано першим аудіофайлом. ' if purpose==2 else
                   'Демонтаж умовно датовано початком останнього аудіофайла за вказівкою користувача; фактичний час знімання не підтверджено. ')
                + f'Наявно {r["n"]} аудіофайлів. Добовий режим невідомий; '
                  f'поле годин ({hours}) є округленою вгору максимальною наявною добовою тривалістю, '
                  'нижньою оцінкою режиму, а не підтвердженим розкладом. '
                  'Модель приладу та місткість SD невідомі; статус SD «залишено наявну» є технічною заглушкою. '
                + ('Батареї: чорні NiMH 2500 мАг, як у попередньому відновленні за вказівкою користувача. ' if purpose==2 else '')
                + ('Перший файл датований 2024 роком, інші дані переважно 2025 року; дату збережено без виправлення. ' if r['location_id']==367 else '')
                + ('Лише один файл: час встановлення та демонтажу збігається. ' if r['n']==1 else '')
                + tag)
            params=dict(l=r['location_id'],dt=dt,p=purpose,h=hours,b=1 if purpose==2 else None,comment=comment)
            print(json.dumps(dict(params,comment=tag),default=str,ensure_ascii=False))
            if apply:
                c.execute(text('''INSERT INTO service_visits
                    (location_id,user_id,visit_datetime,visit_purpose_id,battery_type_id,
                     sd_card_status_id,recording_hours_per_day,comments,device_type_id,is_operational)
                    VALUES(:l,1,:dt,:p,:b,1,:h,:comment,NULL,NULL)'''),params)
    print('APPLIED' if apply else 'DRY RUN')
