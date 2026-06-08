"""
Додає колонки відстеження стану асинхронного перерахунку аналітики в
calculation_log (ct_db): status / started_at / error_message.

Запуск з кореня проекту:
    venv/bin/python -m scripts.init_analytics_status        # Linux / прод
    venv/Scripts/python -m scripts.init_analytics_status    # Windows / dev

Скрипт ідемпотентний:
    • ADD COLUMN IF NOT EXISTS — повторний запуск безпечний.

Навіщо окремо (а не через create_all):
    ct_db не керується Alembic, а CTBase.metadata.create_all() НЕ додає
    нові колонки до вже існуючої таблиці — лише створює відсутні таблиці.
    calculation_log існує здавна, тож колонки треба долити цим ALTER-ом.
    На новій/dev-БД таблиця з колонками створиться через create_all —
    скрипт там просто нічого не змінить.
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE calculation_log ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'idle'",
    "ALTER TABLE calculation_log ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
    "ALTER TABLE calculation_log ADD COLUMN IF NOT EXISTS error_message TEXT",
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
                print(f"  > {stmt[:90]}{'...' if len(stmt) > 90 else ''}")
                conn.execute(text(ddl))
        print()
        print("Готово.")


if __name__ == '__main__':
    main()
