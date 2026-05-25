"""
Створює таблицю cleanup_log у ct_db (журнал dry-run/execute очищення сиріт).

Запуск з кореня проекту:
    venv/bin/python -m scripts.init_cleanup_log        # Linux / прод
    venv/Scripts/python -m scripts.init_cleanup_log    # Windows / dev

Скрипт ідемпотентний:
    • CREATE TABLE IF NOT EXISTS — повторний запуск безпечний
    • CREATE INDEX IF NOT EXISTS — повторний запуск безпечний

Чому окремо від init_ai_tables / init_fast_upload:
    ct_db історично не керується Alembic — лише CTBase.metadata.create_all(),
    який не додає нові таблиці/індекси на існуючу БД ідеально для production
    (особливо коли таблиць багато й хочеться явного контролю).
"""

import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.database import get_ct_engine


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS cleanup_log (
        id                      VARCHAR(36) PRIMARY KEY,
        kind                    VARCHAR(20) NOT NULL,
        status                  VARCHAR(20) NOT NULL,
        triggered_by            INTEGER NOT NULL,
        started_at              TIMESTAMP NOT NULL DEFAULT NOW(),
        finished_at             TIMESTAMP,
        threshold_hours         INTEGER NOT NULL DEFAULT 0,
        report_json             JSONB,
        batches_examined        INTEGER,
        batches_marked_failed   INTEGER,
        photos_deleted          INTEGER,
        files_deleted           INTEGER,
        bytes_freed             BIGINT,
        error_message           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cleanup_log_started ON cleanup_log (started_at)",
    "CREATE INDEX IF NOT EXISTS idx_cleanup_log_status ON cleanup_log (status)",
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
