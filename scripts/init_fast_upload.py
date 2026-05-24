"""
Підготовка ct_db до швидкого завантаження /upload-fast.

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.init_fast_upload          # Windows
    venv/bin/python -m scripts.init_fast_upload              # Linux

Що робить:
    1. Підключається до ct_db через CT_DATABASE_URI.
    2. CREATE INDEX IF NOT EXISTS idx_photos_batch_captured
       ON photos(upload_batch_id, captured_at, id)
       — обовʼязковий для CTE-групування (LAG OVER ORDER BY captured_at).

Скрипт ідемпотентний: повторний запуск без помилок.

Чому окремо від init_ai_tables і не через Alembic:
    ct_db історично не керується Alembic — лише CTBase.metadata.create_all().
    create_all() НЕ додає індекси на існуючі таблиці. Тому одноразовий
    DDL-скрипт. Якщо коли-небудь ct_db переведеться на Alembic — цей
    індекс має увійти у відповідну ревізію.
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    """
    CREATE INDEX IF NOT EXISTS idx_photos_batch_captured
        ON photos (upload_batch_id, captured_at, id)
    """,
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
