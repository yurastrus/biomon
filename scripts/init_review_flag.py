"""
Додає колонки прапорця «на повторний розгляд» до observations (Idea 6).

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.init_review_flag          # Windows
    venv/bin/python -m scripts.init_review_flag              # Linux

Що робить (ідемпотентно, ADD COLUMN IF NOT EXISTS):
    observations.flagged   BOOLEAN NOT NULL DEFAULT FALSE
    observations.flag_note TEXT

Зворотно-сумісно: нові колонки nullable/default — наявний код, що їх не
знає (зокрема /var/www/myproject зі старим shared-ct), працює без змін.
ВАЖЛИВО: застосувати на проді ОДНОЧАСНО з деплоєм коду, що використовує
прапорець (інакше нова логіка впаде на відсутній колонці).

Чому не Alembic: ct_db історично не керується Alembic — лише
CTBase.metadata.create_all() + одноразові DDL-скрипти (як init_fast_upload,
init_query_indexes).
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE observations ADD COLUMN IF NOT EXISTS "
    "flagged BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE observations ADD COLUMN IF NOT EXISTS flag_note TEXT",
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
                print(f"  > {stmt}")
                conn.execute(text(ddl))
        print()
        print("Готово.")


if __name__ == '__main__':
    main()
