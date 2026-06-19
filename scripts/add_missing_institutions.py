# SPDX-License-Identifier: AGPL-3.0-only
"""
Adds the Polissia + Boikivshchyna institutions (parks) missing from the
institutions table, so deployments of these parks from the ARD Excel can be
linked during import.

Run from the project root:
    venv/Scripts/python -m scripts.add_missing_institutions

Idempotent: an institution with an existing `code` is skipped.
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
                print(f"  skip  {code} (already exists)")
                skipped += 1
                continue
            db.session.add(Institution(name_uk=uk, name_en=en, code=code,
                                       ecoregion_en=eco_en, ecoregion_uk=eco_uk))
            print(f"  +     {code}  {en}")
            created += 1
        db.session.commit()
        print(f"\nCreated: {created}, skipped: {skipped}")


if __name__ == '__main__':
    main()
