"""
Set battery_types.estimated_recording_hours (PAM DB) to field values.

These are the fallback capacities of the service forecast (service_forecast.py),
used when a battery type has fewer than 3 recent completed runs. Sources
(2026-10-26): Song Meter Micro Summary logs (scripts/analyze_songmeter_battery.py,
NiMH 2500 median 261 h over 76 runs) and the service log (lithium AA bought in
2026, Varta: median 339 h over 12 runs; the Energizer/Pixel cells used before
gave ~450 h). NiMH 2000/1900 are scaled from 2500 by capacity.

Previous values: 1=300, 2=240, 3=220, 4=450. Idempotent.

Run (tunnel 5433 = prod PAM DB):
    venv/Scripts/python -m scripts.set_pam_battery_field_hours [--dry-run]
"""

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

FIELD_HOURS = {
    1: 260,   # NiMH 2500 mAh
    2: 210,   # NiMH 2000 mAh
    3: 200,   # NiMH 1900 mAh
    4: 340,   # lithium AA (Varta, 2026)
}


def main(dry_run):
    engine = create_engine(os.environ['PAM_DATABASE_URL'])
    with engine.begin() as conn:
        for type_id, hours in FIELD_HOURS.items():
            row = conn.execute(text("SELECT name_ua, estimated_recording_hours FROM battery_types WHERE id = :id"),
                               {'id': type_id}).fetchone()
            print(f'{type_id} {row.name_ua}: {row.estimated_recording_hours} -> {hours}')
            if not dry_run:
                conn.execute(text("UPDATE battery_types SET estimated_recording_hours = :h WHERE id = :id"),
                             {'h': hours, 'id': type_id})
    print('dry run, nothing written' if dry_run else 'done')


if __name__ == '__main__':
    main('--dry-run' in sys.argv)
