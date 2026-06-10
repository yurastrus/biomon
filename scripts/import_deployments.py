"""
Import deployments from the ARD Excel file into ct_db.

Place the file in the project root (CT_LocationARD_Dataset.xlsx). Replace it
with an updated version under the same name and re-run — import is idempotent
(updates existing deployments by deployment_id, inserts new ones).

Run from the project root:
    venv/Scripts/python -m scripts.import_deployments                     # write (existing locations only)
    venv/Scripts/python -m scripts.import_deployments --create-locations  # also create missing locations
    venv/Scripts/python -m scripts.import_deployments --dry-run           # report only, no writes
    venv/Scripts/python -m scripts.import_deployments --file other.xlsx

Without --create-locations only deployments whose coordinates already exist
as locations are imported. With --create-locations the remaining ones get new
location rows (name = deployment_id) linked to the institution by park name.
"""
import argparse
import os

from app import create_app
from app.models import Institution
from app.camera_traps.database import get_ct_session, close_ct_session
from app.camera_traps.deployment_import import (
    import_deployments, format_report, normalize_header,
)

DEFAULT_XLSX = 'CT_LocationARD_Dataset.xlsx'

# Approved mapping: park name in Excel (various spellings) -> institution code in DB.
PARK_NAME_TO_CODE = {
    'carpathian biosphere reserve': 'KBR', 'karpatskyi br': 'KBR',
    'carpathian nnp': 'CNNP', 'karpatskyi nnp': 'CNNP',
    'cheremoski nnp': 'CHNNP', 'cheremoskyi nnp': 'CHNNP',
    'gorgany nature reserve': 'GSNR', 'gorhany nr': 'GSNR',
    'hutsulschyna nnp': 'HNNP', 'hutsulshchyna nnp': 'HNNP',
    'nobelskyi nnp': 'NNNP',
    'prypiat-stokhid nnp': 'PSNNP',
    'skolivski beskydy nnp': 'SBNNP',
    'synevyr nnp': 'SNNP',
    'syniohora nnp': 'SHNNP', 'synohora nnp': 'SHNNP',
    'uzhanski nnp': 'UNNP', 'uzhanskyi nnp': 'UNNP',
    'verkhovinski nnp': 'VNNP', 'verkhovynskyi nnp': 'VNNP',
    'vyzhnitski nnp': 'VZNNP', 'vyzhnystki nnp': 'VZNNP', 'vyzhnytskyi nnp': 'VZNNP',
    'yavorivski nnp': 'YNNP', 'yavorivskyi nnp': 'YNNP',
    'zacharovany kraii nnp': 'ZKNNP', 'zacharovanyi krai nnp': 'ZKNNP',
    # added Polissia + Boikivshchyna institutions
    'boikivshchyna nnp': 'BNNP',
    'cheremskyi nr': 'CHNR',
    'chornobyl radiation and ecological br': 'CREBR',
    'drevlianskyi nr': 'DNR',
    'poliskyi nr': 'PNR',
    'pushcha radzivila nnp': 'PRNNP',
}


def build_park_institution_map():
    """Build {normalized park name -> institution_id} using the approved mapping."""
    code_to_id = {i.code: i.id for i in Institution.query.all()}
    result = {}
    missing_codes = set()
    for park_norm, code in PARK_NAME_TO_CODE.items():
        if code in code_to_id:
            result[normalize_header(park_norm)] = code_to_id[code]
        else:
            missing_codes.add(code)
    if missing_codes:
        print(f"УВАГА: у БД немає установ з кодами: {sorted(missing_codes)}")
    return result


def main():
    parser = argparse.ArgumentParser(description='Імпорт деплойментів з Екселю')
    parser.add_argument('--file', default=DEFAULT_XLSX, help='шлях до .xlsx')
    parser.add_argument('--dry-run', action='store_true', help='без запису в БД')
    parser.add_argument('--create-locations', action='store_true',
                        help='створювати локації для деплойментів без наявної точки')
    parser.add_argument('--sheets', nargs='*', help='конкретні листи (за замовч. усі дані)')
    args = parser.parse_args()

    xlsx = args.file if os.path.isabs(args.file) else os.path.join(os.getcwd(), args.file)

    app = create_app()
    with app.app_context():
        park_map = build_park_institution_map() if args.create_locations else None
        session = get_ct_session()
        try:
            report = import_deployments(
                session, xlsx, sheets=args.sheets, dry_run=args.dry_run,
                create_missing_locations=args.create_locations,
                park_institution_map=park_map,
            )
            print(format_report(report))
        finally:
            close_ct_session()


if __name__ == '__main__':
    main()
