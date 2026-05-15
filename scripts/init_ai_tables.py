"""
Створює таблиці AI-runner'а в базі ct_db.

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.init_ai_tables          # Windows
    venv/bin/python -m scripts.init_ai_tables              # Linux

Або з прапором --drop для пересоздання (УВАГА: видаляє всі дані прогнозів):
    python -m scripts.init_ai_tables --drop

Що робить:
    1. Підключається до ct_db через CT_DATABASE_URI з .env
    2. Перевіряє які з 3 таблиць (ai_models, ai_predictions, ai_run_queue) уже існують
    3. Створює відсутні (`CREATE TABLE IF NOT EXISTS` через SQLAlchemy)
    4. Виводить підсумок

Скрипт є ідемпотентним: повторний запуск без --drop безпечний.
"""

import argparse
import sys

from sqlalchemy import inspect

from app import create_app
from app.camera_traps.database import get_ct_engine
from app.camera_traps.models import AIModel, AIPrediction, AIRunQueue


AI_TABLES = [AIModel.__table__, AIPrediction.__table__, AIRunQueue.__table__]


def main():
    parser = argparse.ArgumentParser(description='Init AI-runner tables in ct_db')
    parser.add_argument(
        '--drop',
        action='store_true',
        help='УВАГА: видалити існуючі AI-таблиці перед створенням (втрата даних)',
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        engine = get_ct_engine()

        inspector = inspect(engine)
        existing = set(inspector.get_table_names())

        print(f"Connected to: {engine.url}")
        print()

        if args.drop:
            confirm = input(
                f"Це видалить таблиці {[t.name for t in AI_TABLES]} і всі дані в них.\n"
                f"Введи 'DROP' для підтвердження: "
            )
            if confirm != 'DROP':
                print("Скасовано.")
                sys.exit(1)
            # порядок drop важливий: спершу залежні (predictions посилається на models)
            for table in reversed(AI_TABLES):
                if table.name in existing:
                    print(f"  DROP TABLE {table.name}")
                    table.drop(engine)

        # CREATE: refresh inspector після можливого drop
        existing = set(inspect(engine).get_table_names())

        print("Стан таблиць:")
        to_create = []
        for table in AI_TABLES:
            if table.name in existing:
                print(f"  ✓ {table.name} (уже існує)")
            else:
                print(f"  + {table.name} (буде створено)")
                to_create.append(table)

        if not to_create:
            print("\nНемає чого створювати.")
            return

        print()
        print(f"Створюю {len(to_create)} таблиць...")
        for table in to_create:
            table.create(engine)
            print(f"  ✓ {table.name}")

        # Перевірка
        existing_after = set(inspect(engine).get_table_names())
        for table in to_create:
            if table.name not in existing_after:
                print(f"  ✗ ПОМИЛКА: {table.name} не з'явилася в БД")
                sys.exit(2)

        print("\nГотово.")


if __name__ == '__main__':
    main()
