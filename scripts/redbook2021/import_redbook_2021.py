# SPDX-License-Identifier: AGPL-3.0-only
"""
Write Red Data Book of Ukraine (2021) statuses into the CT and PAM species
tables, column `species.redbook2021` in ct_db and pam_db.

    venv/Scripts/python -m scripts.redbook2021.import_redbook_2021            # dry run
    venv/Scripts/python -m scripts.redbook2021.import_redbook_2021 --apply    # write

Dry run is the default and only reads. --apply needs the column to exist
(ct: scripts.init_redbook2021; pam: migration 0008).

Scope: Amphibia, Aves, Mammalia from redbook2021_animals.csv (built by
extract_redbook_2021.py from the ministry order No. 29 of 19.01.2021).

Stored value: a language-neutral category code (CATEGORY_CODES in the
extractor), NULL = not in the Red Book. The importer only sets codes on
matched species and clears codes on species that no longer match, so a
re-run after a list or name change converges.

Matching, in order:
  1. exact binomial (case-insensitive);
  2. SYNONYMS: typos in the order itself and names the DB spells differently
     after taxonomic updates (both directions are tried);
  3. Ukrainian common name, reported for review only, never written.
"""

import argparse
import csv
from pathlib import Path

from sqlalchemy import inspect, text

CSV_PATH = Path(__file__).with_name('redbook2021_animals.csv')
CLASSES = ('Amphibia', 'Aves', 'Mammalia')
COLUMN = 'redbook2021'

# Name in the order -> other names the same taxon may carry in our DBs.
SYNONYMS = {
    # typos in the order
    'Haliaeetus allbicilla': ['Haliaeetus albicilla'],
    'Gallingo media': ['Gallinago media'],
    'Denrocopos leucotos': ['Dendrocopos leucotos'],
    # taxonomy drift (order name -> current / alternative names)
    'Rufibrenta ruficollis': ['Branta ruficollis'],
    'Cygnus bewickii': ['Cygnus columbianus', 'Cygnus columbianus bewickii'],
    'Anas strepera': ['Mareca strepera'],
    'Phalacrocorax aristotelis': ['Gulosus aristotelis'],
    'Phalacrocorax pygmaeus': ['Microcarbo pygmaeus', 'Microcarbo pygmeus'],
    'Hieraaetus pennatus': ['Aquila pennata'],
    'Aquila rapax': ['Aquila nipalensis'],
    'Aquila clanga': ['Clanga clanga'],
    'Aquila pomarina': ['Clanga pomarina'],
    'Lyrurus tetrix': ['Tetrao tetrix'],
    'Anthropoides virgo': ['Grus virgo'],
    'Larus ichthyaetus': ['Ichthyaetus ichthyaetus'],
    'Sterna albifrons': ['Sternula albifrons'],
    'Hydroprogne caspia': ['Sterna caspia'],
    'Sturnus roseus': ['Pastor roseus'],
    'Parus cyanus': ['Cyanistes cyanus'],
    'Regulus ignicapillus': ['Regulus ignicapilla'],
    'Calandrella rufescens': ['Alaudala rufescens'],
    'Epidalea calamita': ['Bufo calamita'],
    'Ichthyosaura alpestris': ['Triturus alpestris', 'Mesotriton alpestris'],
    'Lissotriton montandoni': ['Triturus montandoni'],
    'Nothocricetulus migratorius': ['Cricetulus migratorius'],
    'Myotis aurascens': ['Myotis davidii'],
    'Mustela eversmanii': ['Mustela eversmannii'],
    'Equus gmelini': ['Equus ferus'],
    'Bison bonasus': ['Bos bonasus'],
}


def load_list():
    with CSV_PATH.open(encoding='utf-8') as f:
        return [r for r in csv.DictReader(f) if r['class'] in CLASSES]


def _norm_ua(s):
    return ' '.join((s or '').lower().replace('’', "'").replace('ʼ', "'").split())


def _binomial(s):
    return ' '.join((s or '').split()[:2]).lower()


def plan_module(redlist, species, has_column):
    """Pure matching. `species`: dicts with id, scientific_name, name_ua, class, current.

    Returns (matches, ua_only, clears, missing) where matches are
    (species, list_row, how) with how in {'exact', 'synonym'}.
    """
    by_name = {}
    for sp in species:
        by_name.setdefault(_binomial(sp['scientific_name']), []).append(sp)

    matches, matched_ids, missing = [], set(), []
    for row in redlist:
        sp_hits = [(sp, 'exact') for sp in by_name.get(_binomial(row['scientific_name']), [])]
        if not sp_hits:
            for alt in SYNONYMS.get(row['scientific_name'], []):
                sp_hits += [(sp, 'synonym') for sp in by_name.get(_binomial(alt), [])]
        if not sp_hits:
            missing.append(row)
        for sp, how in sp_hits:
            if sp['id'] not in matched_ids:
                matches.append((sp, row, how))
                matched_ids.add(sp['id'])

    by_ua = {}
    for sp in species:
        if sp['id'] not in matched_ids and sp['name_ua']:
            by_ua.setdefault(_norm_ua(sp['name_ua']), []).append(sp)
    ua_only = [(sp, row) for row in missing for sp in by_ua.get(_norm_ua(row['name_ua']), [])]

    clears = [sp for sp in species
              if has_column and sp['current'] and sp['id'] not in matched_ids]
    return matches, ua_only, clears, missing


def read_species(engine, id_col, ua_col):
    has_column = COLUMN in {c['name'] for c in inspect(engine).get_columns('species')}
    current = COLUMN if has_column else 'NULL'
    sql = (f'SELECT {id_col} AS id, scientific_name, {ua_col} AS name_ua, '
           f'"class" AS cls, {current} AS current FROM species')
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [dict(r, **{'class': r['cls']}) for r in rows], has_column


def apply(engine, id_col, matches, clears):
    with engine.begin() as conn:
        for sp, row, _ in matches:
            conn.execute(text(f'UPDATE species SET {COLUMN} = :c WHERE {id_col} = :i'),
                         {'c': row['category'], 'i': sp['id']})
        for sp in clears:
            conn.execute(text(f'UPDATE species SET {COLUMN} = NULL WHERE {id_col} = :i'),
                         {'i': sp['id']})


def report(label, matches, ua_only, clears, missing, has_column, total):
    print(f'\n=== {label}: {total} species in table, column {COLUMN} '
          f'{"exists" if has_column else "MISSING"} ===')
    print(f'Matched {len(matches)}:')
    for sp, row, how in sorted(matches, key=lambda m: (m[1]['class'], m[0]['scientific_name'])):
        change = '' if sp['current'] == row['category'] else f'  [{sp["current"]} -> {row["category"]}]'
        tag = '' if how == 'exact' else f'  (via synonym: list "{row["scientific_name"]}")'
        print(f'  {row["class"][:4]}  {sp["scientific_name"]:<32} {sp["name_ua"] or "":<28} '
              f'{row["category"]}{tag}{change}')
    if ua_only:
        print('Same Ukrainian name, different Latin (NOT written, check by hand):')
        for sp, row in ua_only:
            print(f'  db "{sp["scientific_name"]}" / list "{row["scientific_name"]}"  {row["name_ua"]}')
    if clears:
        print(f'Would clear {len(clears)} stale value(s):')
        for sp in clears:
            print(f'  {sp["scientific_name"]} ({sp["current"]})')
    print(f'List taxa absent from this table: {len(missing) - len(ua_only)}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--apply', action='store_true', help='write (default: dry run)')
    ap.add_argument('--module', choices=('ct', 'pam', 'both'), default='both')
    args = ap.parse_args()

    from app import create_app
    from app.camera_traps.database import get_ct_engine
    from app.pam.utils import get_pam_engine

    redlist = load_list()
    print(f'Red Book 2021 list: {len(redlist)} taxa in {", ".join(CLASSES)}')
    targets = [('ct', 'ct_db', get_ct_engine, 'id', 'common_name_ua'),
               ('pam', 'pam_db', get_pam_engine, 'species_id', 'common_name_uk')]

    app = create_app()
    with app.app_context():
        for key, label, get_engine, id_col, ua_col in targets:
            if args.module not in (key, 'both'):
                continue
            engine = get_engine()
            species, has_column = read_species(engine, id_col, ua_col)
            matches, ua_only, clears, missing = plan_module(redlist, species, has_column)
            report(label, matches, ua_only, clears, missing, has_column, len(species))
            if args.apply:
                if not has_column:
                    raise SystemExit(f'{label}: column {COLUMN} missing, create it first')
                apply(engine, id_col, matches, clears)
                print(f'{label}: written.')
    if not args.apply:
        print('\nDry run, nothing written. Re-run with --apply to write.')


if __name__ == '__main__':
    main()
