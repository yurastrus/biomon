"""User-requested retrospective service log reconstruction; dry run unless --apply.

Audio timestamps are retained as stored, not reinterpreted as local wall time.
Unknown Potelych device/card capacity are not inferred from other deployments.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
APPLY = '--apply' in sys.argv
IDS = [402, 403, 406, 407, 408]
NOTE = 'Запис відновлено постфактум на основі даних про аудіофайли.'

with create_engine(os.environ['PAM_DATABASE_URL']).begin() as conn:
    if not APPLY:
        conn.execute(text('SET TRANSACTION READ ONLY'))
    sources = conn.execute(text('''
        SELECT location_id, min(datetime_start) first_audio,
               max(datetime_start) last_audio
        FROM recordings WHERE location_id = ANY(:ids) GROUP BY location_id
        ORDER BY location_id
    '''), {'ids': IDS}).mappings().all()
    assert len(sources) == 5
    for source in sources:
        loc = source['location_id']
        for purpose, dt in [(2, source['first_audio']), (3, source['last_audio'])]:
            existing = conn.execute(text('''SELECT * FROM service_visits
                WHERE location_id=:loc AND visit_purpose_id=:purpose
                ORDER BY visit_datetime FOR UPDATE
            ''' if APPLY else '''SELECT * FROM service_visits
                WHERE location_id=:loc AND visit_purpose_id=:purpose
                ORDER BY visit_datetime'''), {'loc': loc, 'purpose': purpose}).mappings().all()
            assert len(existing) <= 1, (loc, purpose, 'ambiguous history')
            old = dict(existing[0]) if existing else None
            comment = NOTE + (' Встановлення датовано першим аудіофайлом. '
                'Режим оцінено приблизно у 6 год/добу: ранковий і вечірній блоки '
                'та 5-хвилинні записи приблизно щогодини поза ними. '
                'Батареї: чорні акумулятори NiMH 2500 мАг (зі слів користувача).'
                if purpose == 2 else
                ' Демонтаж умовно датовано початком останнього аудіофайла за '
                'вказівкою користувача; фактичну дату знімання не підтверджено. '
                'Оцінений режим до демонтажу: близько 6 год/добу.')
            if purpose == 2 and not old:
                comment += (' Модель приладу та місткість SD-картки невідомі; '
                    'статус «залишено наявну» використано як технічну заглушку '
                    'обов’язкового поля SD, а не підтверджений факт.')
            if old and old.get('comments') and NOTE not in old['comments']:
                comment = old['comments'] + '\n' + comment
            params = dict(loc=loc, purpose=purpose, dt=dt,
                battery=1 if purpose == 2 else None,
                sd=old['sd_card_status_id'] if old and purpose == 2 else 1,
                # Production CHECK still requires 1..24 even for removal.
                # Retain the preceding schedule; purpose=3 marks removal.
                hours=6,
                device=old['device_type_id'] if old and purpose == 2 else None,
                comment=comment)
            print(json.dumps({'before': old, 'after': params}, default=str, ensure_ascii=False))
            if APPLY:
                if old:
                    conn.execute(text('''UPDATE service_visits SET
                        visit_datetime=:dt,battery_type_id=:battery,
                        sd_card_status_id=:sd,recording_hours_per_day=:hours,
                        device_type_id=:device,comments=:comment WHERE id=:id
                    '''), dict(params, id=old['id']))
                else:
                    conn.execute(text('''INSERT INTO service_visits
                        (location_id,user_id,visit_datetime,visit_purpose_id,
                         battery_type_id,sd_card_status_id,recording_hours_per_day,
                         device_type_id,comments,is_operational)
                        VALUES (:loc,1,:dt,:purpose,:battery,:sd,:hours,:device,:comment,NULL)
                    '''), params)
    print('APPLIED' if APPLY else 'DRY RUN')
