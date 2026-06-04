"""
Додає колонку тривалості запису до recordings (PAM coverage #37).

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.init_recording_duration          # Windows
    venv/bin/python -m scripts.init_recording_duration              # Linux

Що робить (ідемпотентно):
    recordings.duration_minutes NUMERIC(6,2) NOT NULL DEFAULT 5

    DEFAULT 5 ОДРАЗУ заповнює всі наявні рядки значенням 5 (усі записи в
    системі — 5-хвилинні), тож окремий backfill не потрібен. Надалі тривалість
    проставляється при імпорті (поле у формі pam/import).

Зворотно-сумісно (NOT NULL DEFAULT — наявний код, що не знає колонки, працює).
Застосувати на проді РАЗОМ із деплоєм коду #37.

Чому не Alembic: pam_db історично не керується Alembic — одноразові DDL-скрипти.
"""

from sqlalchemy import text

from app import create_app
from app.pam.utils import get_pam_engine


DDL_STATEMENTS = [
    "ALTER TABLE recordings ADD COLUMN IF NOT EXISTS "
    "duration_minutes NUMERIC(6,2) NOT NULL DEFAULT 5",
]


def main():
    app = create_app()
    with app.app_context():
        engine = get_pam_engine()
        print(f"Connected to: {engine.url}")
        print()
        with engine.begin() as conn:
            for ddl in DDL_STATEMENTS:
                stmt = ' '.join(ddl.split())
                print(f"  > {stmt}")
                conn.execute(text(ddl))
        print()
        print("Готово.")


if __name__ == '__main__':
    main()
