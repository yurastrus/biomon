"""
Створення індексів під часті запити ct_db (dashboard, cleanup, contributor).

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.init_query_indexes          # Windows
    venv/bin/python -m scripts.init_query_indexes              # Linux

Що робить:
    1. Підключається до ct_db через CT_DATABASE_URI.
    2. CREATE INDEX IF NOT EXISTS idx_photos_status
       ON photos(status)            — cleanup (status='completed'/'pending'),
                                       dashboard photo-status фільтри.
    3. CREATE INDEX IF NOT EXISTS idx_identifications_user_id
       ON identifications(user_id)  — dashboard top-contributors (GROUP BY
                                       user_id), сторінка внеску користувача.

Скрипт ідемпотентний: повторний запуск без помилок.

ВАЖЛИВО про прод:
    На проді (станом на 2026-06) ОБИДВА індекси вже існують і
    використовуються (перевірено EXPLAIN ANALYZE: Index/Bitmap Index Scan).
    Тобто на наявному ct_db цей скрипт нічого не змінить (IF NOT EXISTS).
    Він потрібен лише для НОВИХ/dev-інсталяцій, де таблиці малі.

    Якщо колись доведеться будувати ці індекси на вже великій таблиці
    в проді — НЕ робити це звичайним CREATE INDEX (ACCESS EXCLUSIVE lock
    блокує читання/запис на час побудови). Натомість поза транзакцією:
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_photos_status
            ON photos(status);
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_identifications_user_id
            ON identifications(user_id);

Чому окремо від init_fast_upload і не через Alembic:
    ct_db історично не керується Alembic — лише CTBase.metadata.create_all().
    create_all() НЕ додає індекси на існуючі таблиці. Тому одноразовий
    DDL-скрипт (той самий патерн, що scripts/init_fast_upload.py).
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_photos_status ON photos (status)",
    "CREATE INDEX IF NOT EXISTS idx_identifications_user_id "
    "ON identifications (user_id)",
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
