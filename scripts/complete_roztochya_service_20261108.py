"""Apply user-requested Roztochya service corrections; dry-run by default."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
APPLY = '--apply' in sys.argv
IDS = [12,33,59,89,110,218,222,255,258,264,267,279,282]
TAG = '[roztochya-service-2026-11-08]'
with create_engine(os.environ['PAM_DATABASE_URL']).begin() as c:
    if not APPLY:
        c.execute(text('SET TRANSACTION READ ONLY'))
    for loc in IDS:
        first = c.execute(text('SELECT min(datetime_start) FROM recordings WHERE location_id=:l'),{'l':loc}).scalar_one()
        assert first is not None
        exists = c.execute(text('SELECT id FROM service_visits WHERE location_id=:l AND visit_purpose_id=2'),{'l':loc}).fetchall()
        if exists:
            print('Installation exists',loc)
            continue
        hours = c.execute(text('SELECT recording_hours_per_day FROM service_visits WHERE location_id=:l ORDER BY visit_datetime,id LIMIT 1'),{'l':loc}).scalar_one()
        comment = ('Встановлення відновлено постфактум за першим наявним аудіофайлом за вказівкою користувача. '
            'Дата й час є межею наявних даних, фактичний час монтажу не підтверджено. '
            'Батареї: чорні акумулятори NiMH 2500 мАг, зі слів користувача. '
            'Години запису перенесено з найранішого наявного сервісного візиту; початковий режим не підтверджено. '
            'Модель приладу та місткість SD невідомі; SD «залишено наявну» є технічною заглушкою. '+TAG)
        print('INSTALL',loc,first,hours)
        if APPLY:
            c.execute(text('''INSERT INTO service_visits
                (location_id,user_id,visit_datetime,visit_purpose_id,battery_type_id,
                 sd_card_status_id,recording_hours_per_day,comments)
                VALUES(:l,1,:dt,2,1,1,:h,:comment)'''),dict(l=loc,dt=first,h=hours,comment=comment))

    first = c.execute(text('SELECT min(datetime_start) FROM recordings WHERE location_id=420')).scalar_one()
    old = c.execute(text('SELECT visit_datetime FROM service_visits WHERE id=112 AND location_id=420 AND visit_purpose_id=2')).scalar_one()
    print('ZHORNYSKA',old,'->',first)
    if APPLY:
        c.execute(text('''UPDATE service_visits SET visit_datetime=:dt,
            comments=concat_ws(E'\n',nullif(comments,''),:comment)
            WHERE id=112 AND location_id=420 AND visit_purpose_id=2
              AND visit_datetime IS DISTINCT FROM :dt'''),dict(dt=first,comment=
            f'Дату й час встановлення виправлено за першим аудіофайлом за вказівкою користувача; попередня мітка: {old}. '+TAG))

    print('BATTERY visit 7, platform 59 -> NiMH 2500')
    if APPLY:
        c.execute(text('''UPDATE service_visits SET battery_type_id=1,
            comments=concat_ws(E'\n',nullif(comments,''),:comment)
            WHERE id=7 AND location_id=59 AND visit_purpose_id<>3
              AND battery_type_id IS DISTINCT FROM 1'''),dict(comment=
            'Батареї позначено як чорні NiMH 2500 мАг за вказівкою користувача постфактум; фактичний тип не підтверджений. '+TAG))

    name = 'Розточчя - Верещицьке ПОНДВ - біля фотопастки'
    near = c.execute(text('SELECT location_id FROM locations WHERE abs(lat-49.95417)<0.00001 AND abs(lon-23.64582)<0.00001')).fetchall()
    assert len(near)<=1
    print('LOCATION',name,'49.95417,23.64582; install 2026-07-04 12:00 Europe/Kyiv; 6 h; NiMH2500')
    if APPLY:
        if near:
            lid=near[0][0]
        else:
            lid=c.execute(text('''INSERT INTO locations
                (location_name,location_name_en,lat,lon,geom,visibility_level,state_province)
                VALUES(:name,:en,49.95417,23.64582,ST_SetSRID(ST_MakePoint(23.64582,49.95417),4326),0,:region)
                RETURNING location_id'''),dict(name=name,en='Roztochya - Vereshchytske ranger district - near camera trap',region='Львівська область')).scalar_one()
            c.execute(text('INSERT INTO location_institutions(location_id,institution_id) VALUES(:l,1)'),{'l':lid})
        exists=c.execute(text('SELECT 1 FROM service_visits WHERE location_id=:l AND visit_purpose_id=2'),{'l':lid}).first()
        if not exists:
            c.execute(text('''INSERT INTO service_visits
                (location_id,user_id,visit_datetime,visit_purpose_id,battery_type_id,
                 sd_card_status_id,recording_hours_per_day,comments)
                VALUES(:l,1,TIMESTAMP '2026-07-04 12:00:00' AT TIME ZONE 'Europe/Kyiv',2,1,1,6,:comment)'''),dict(l=lid,comment=
                'Встановлено 04.07.2026 зі слів користувача. Час невідомий; умовно 12:00 Europe/Kyiv. '
                '6 годин запису на добу, чорні акумулятори NiMH 2500 мАг. '
                'Модель приладу та місткість SD невідомі; SD «залишено наявну» є технічною заглушкою. '+TAG))
        print('LOCATION ID',lid)
    print('APPLIED' if APPLY else 'DRY RUN')
