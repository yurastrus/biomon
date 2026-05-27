"""
Імпорт деплойментів з ARD-Екселю у ct_db.

Ексель лежить у корені проекту (CT_LocationARD_Dataset.xlsx). Заміни файл на
свіжий з тим самим іменем і запусти скрипт ще раз — імпорт ідемпотентний
(оновлює наявні деплойменти за deployment_id, нові додає).

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.import_deployments              # запис
    venv/Scripts/python -m scripts.import_deployments --dry-run    # лише звіт
    venv/Scripts/python -m scripts.import_deployments --file other.xlsx

Імпортуються лише деплойменти, чиї координати (до 5 знаків) уже є серед
локацій. Решта потрапляє у діагностичний звіт як 'нема локації'.
"""
import argparse
import os

from app import create_app
from app.camera_traps.database import get_ct_session, close_ct_session
from app.camera_traps.deployment_import import import_deployments, format_report

DEFAULT_XLSX = 'CT_LocationARD_Dataset.xlsx'


def main():
    parser = argparse.ArgumentParser(description='Імпорт деплойментів з Екселю')
    parser.add_argument('--file', default=DEFAULT_XLSX, help='шлях до .xlsx')
    parser.add_argument('--dry-run', action='store_true', help='без запису в БД')
    parser.add_argument('--sheets', nargs='*', help='конкретні листи (за замовч. усі дані)')
    args = parser.parse_args()

    xlsx = args.file if os.path.isabs(args.file) else os.path.join(os.getcwd(), args.file)

    app = create_app()
    with app.app_context():
        session = get_ct_session()
        try:
            report = import_deployments(session, xlsx, sheets=args.sheets, dry_run=args.dry_run)
            print(format_report(report))
        finally:
            close_ct_session()


if __name__ == '__main__':
    main()
