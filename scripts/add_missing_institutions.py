"""
Додає установи (парки) Полісся + Бойківщина, яких бракує в таблиці institutions,
щоб деплойменти цих парків з ARD-Екселю можна було прив'язати під час імпорту.

Запуск з кореня проекту:
    venv/Scripts/python -m scripts.add_missing_institutions

Ідемпотентний: установа з наявним `code` пропускається.
"""
from app import create_app
from app.extensions import db
from app.models import Institution

# (code, name_en, name_uk, ecoregion_en, ecoregion_uk)
MISSING = [
    ('BNNP',  'Boikivshchyna NNP',                     'НПП "Бойківщина"',                       'Carpathians', 'Карпати'),
    ('CHNR',  'Cheremskyi NR',                         'Черемський ПЗ',                          'Polissia',    'Полісся'),
    ('CREBR', 'Chornobyl Radiation and Ecological BR', 'Чорнобильський радіаційно-екологічний БЗ', 'Polissia',  'Полісся'),
    ('DNR',   'Drevlianskyi NR',                       'Древлянський ПЗ',                        'Polissia',    'Полісся'),
    ('PNR',   'Poliskyi NR',                           'Поліський ПЗ',                           'Polissia',    'Полісся'),
    ('PRNNP', 'Pushcha Radzivila NNP',                 'НПП "Пуща Радзивіла"',                   'Polissia',    'Полісся'),
]


def main():
    app = create_app()
    with app.app_context():
        created, skipped = 0, 0
        for code, en, uk, eco_en, eco_uk in MISSING:
            if Institution.query.filter_by(code=code).first():
                print(f"  skip  {code} (вже існує)")
                skipped += 1
                continue
            db.session.add(Institution(name_uk=uk, name_en=en, code=code,
                                       ecoregion_en=eco_en, ecoregion_uk=eco_uk))
            print(f"  +     {code}  {en}")
            created += 1
        db.session.commit()
        print(f"\nСтворено: {created}, пропущено: {skipped}")


if __name__ == '__main__':
    main()
