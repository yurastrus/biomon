"""
Створює таблицю deployments у ct_db (встановлення фотопастки на локації за період).

Запуск з кореня проекту:
    venv/bin/python -m scripts.init_deployments        # Linux / прод
    venv/Scripts/python -m scripts.init_deployments    # Windows / dev

Скрипт ідемпотентний:
    • CREATE TABLE IF NOT EXISTS — повторний запуск безпечний
    • CREATE INDEX IF NOT EXISTS — повторний запуск безпечний

ct_db історично не керується Alembic — лише CTBase.metadata.create_all(),
який не додає нові таблиці/індекси на існуючу БД. Тож DDL тут явний.
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS deployments (
        id                                  SERIAL PRIMARY KEY,
        location_id                         INTEGER REFERENCES locations(id),
        name                                VARCHAR(200) NOT NULL,
        start_date                          DATE,
        end_date                            DATE,
        start_time                          TIME,
        end_time                            TIME,
        study_year                          INTEGER,
        study_season                        VARCHAR(20),
        study_design                        VARCHAR(100),
        camera_id                           VARCHAR(10),
        n_days_working                      INTEGER,
        n_days_calc                         INTEGER GENERATED ALWAYS AS (end_date - start_date) STORED,
        n_photos                            INTEGER,
        camera_model                        VARCHAR(100),
        serial_number                       VARCHAR(100),
        qc_non_functional                   BOOLEAN,
        qc_stolen                           BOOLEAN,
        qc_hardware_issue                   BOOLEAN,
        qc_firmware_issue                   BOOLEAN,
        qc_settings_issue                   BOOLEAN,
        qc_battery_issue                    BOOLEAN,
        qc_sd_issue                         BOOLEAN,
        qc_no_data_uploaded_by_PA           BOOLEAN,
        qc_uploaded_data_is_not_raw         BOOLEAN,
        qc_no_GPS_coordinates               BOOLEAN,
        qc_no_species_captured              BOOLEAN,
        qc_placement_incorrect              BOOLEAN,
        qc_poor_placement                   BOOLEAN,
        qc_feeding_location                 BOOLEAN,
        qc_installation_incorrect           BOOLEAN,
        qc_lapse_photos_missed              BOOLEAN,
        qc_installation_photos_missed       BOOLEAN,
        qc_deinstallation_photos_missed     BOOLEAN,
        qc_distance_reference_photos_missed BOOLEAN,
        qc_datetime_photos_missed           BOOLEAN,
        qc_local_datetime_not_set           BOOLEAN,
        qc_local_datetime_issue             TEXT,
        qc_data_not_usable                  BOOLEAN,
        qc_used_brf                         BOOLEAN,
        qc_comment                          TEXT,
        history_unknown                     BOOLEAN NOT NULL DEFAULT FALSE,
        created_at                          TIMESTAMP DEFAULT NOW(),
        created_by_id                       INTEGER
    )
    """,
    # Розширення camera_id для таблиць, створених раніше з VARCHAR(4) (безпечно повторювати).
    "ALTER TABLE deployments ALTER COLUMN camera_id TYPE VARCHAR(10)",
    # location_id NULL для деплойментів без GPS — щоб включити їх у QC-аналіз як qc_no_gps_coordinates.
    "ALTER TABLE deployments ALTER COLUMN location_id DROP NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_deployments_location ON deployments (location_id)",
    "CREATE INDEX IF NOT EXISTS idx_deployments_loc_dates ON deployments (location_id, start_date, end_date)",
]


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        print(f"Connected to: {engine.url}")
        print()
        with engine.begin() as conn:
            for ddl in DDL_STATEMENTS:
                stmt = ' '.join(ddl.split())
                print(f"  > {stmt[:80]}{'...' if len(stmt) > 80 else ''}")
                conn.execute(text(ddl))
        print()
        print("Готово.")


if __name__ == '__main__':
    main()
