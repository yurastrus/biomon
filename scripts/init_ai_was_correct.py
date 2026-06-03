"""
Додає колонку was_correct до ai_predictions (Idea 4).

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.init_ai_was_correct          # Windows
    venv/bin/python -m scripts.init_ai_was_correct              # Linux

Що робить (ідемпотентно):
    ai_predictions.was_correct BOOLEAN NULL
        — заповнюється у момент консенсусу (mark_observation_complete):
          True/False = прогноз збігся/не збігся з консенсусним видом,
          NULL = ще не оцінено або AI не визначив вид.

Зворотно-сумісно (nullable). Застосувати на проді РАЗОМ з деплоєм коду.
Історичні completed-серії заповнюються окремо: scripts.backfill_was_correct.

Чому не Alembic: ct_db історично не керується Alembic — лише
CTBase.metadata.create_all() + одноразові DDL-скрипти.
"""

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    "ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS was_correct BOOLEAN",
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
