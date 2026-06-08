"""
Видаляє мертву колонку identifications.confidence_level з ct_db (#46).

Колонка була повністю порожня (форма ідентифікації її не заповнювала —
архітектурний залишок, виявлено діагностикою #41).

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.drop_confidence_level          # Windows
    venv/bin/python -m scripts.drop_confidence_level              # Linux

БЕЗПЕКА: скрипт ПОВТОРНО перевіряє, що колонка порожня (COUNT(confidence_level)=0),
і ПЕРЕРИВАЄТЬСЯ без змін, якщо знайде хоч одне непорожнє значення. DROP і
перевірка — в одній транзакції.

УВАГА: НЕ плутати з pam_db.segments.confidence_level (інша БД, активно
використовується). Тут — ЛИШЕ ct_db.identifications.confidence_level.
Застосувати на проді РАЗОМ із деплоєм коду (модель уже без цієї колонки) +
координація /var/www/myproject.
"""

import sys
from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


def main():
    app = create_app()
    with app.app_context():
        engine = get_ct_engine()
        print(f"Connected to: {engine.url}")
        with engine.begin() as conn:
            nn = conn.execute(text(
                "SELECT COUNT(confidence_level) FROM identifications")).scalar()
            total = conn.execute(text(
                "SELECT COUNT(*) FROM identifications")).scalar()
            print(f"confidence_level non-null: {nn} з {total}")
            if nn and nn > 0:
                print("ПЕРЕРВАНО: колонка НЕ порожня — нічого не видалено.")
                sys.exit(1)
            print("  > ALTER TABLE identifications DROP COLUMN IF EXISTS confidence_level")
            conn.execute(text(
                "ALTER TABLE identifications DROP COLUMN IF EXISTS confidence_level"))
        print("Готово: confidence_level видалено.")


if __name__ == '__main__':
    main()
