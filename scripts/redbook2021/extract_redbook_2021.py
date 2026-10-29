# SPDX-License-Identifier: AGPL-3.0-only
"""
Turn the official Red Data Book of Ukraine animal list (2021) into a CSV.

Source: order of the Ministry of Environmental Protection and Natural
Resources of Ukraine of 19.01.2021 No. 29, registered with the Ministry of
Justice 01.03.2021 No. 260/35882, "Перелік видів тварин, що заносяться до
Червоної книги України (тваринний світ)". The Red Book itself was not
re-published after 2021; this list is the current legal version.

The ministry ships it as a legacy .doc. Convert it to .docx once (Word:
Save As .docx, or LibreOffice --convert-to docx), then:

    venv/Scripts/python -m scripts.redbook2021.extract_redbook_2021 <list.docx>

Writes scripts/redbook2021/redbook2021_animals.csv (all 686 taxa, every
class). The importer reads that CSV, so this step is needed only if the
source list changes. Stdlib only.
"""

import csv
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

OUT = Path(__file__).with_name('redbook2021_animals.csv')

# Category wording in the order -> language-neutral code stored in the DB.
CATEGORY_CODES = {
    'зниклий': 'extinct',
    'зниклий в природі': 'extinct_in_wild',
    'зникаючий': 'endangered',
    'вразливий': 'vulnerable',
    'рідкісний': 'rare',
    'неоцінений': 'not_evaluated',
    'недостатньо відомий': 'data_deficient',
}

# Lower-case words that open an authority, e.g. 'Myotis alcathoe von Helversen'.
AUTHOR_PARTICLES = {'von', 'van', 'de', 'del', 'da', 'in', 'et'}

FIELDS = ['no', 'phylum', 'class', 'order', 'scientific_name', 'authority',
          'name_full', 'name_ua', 'category_ua', 'category']


def _cell_text(tc):
    paras = [''.join(t.text or '' for t in p.iter(W + 't')) for p in tc.iter(W + 'p')]
    return ' '.join(p.strip() for p in paras if p.strip())


def split_name(full):
    """'Lutra lutra (Linnaeus, 1758)' -> ('Lutra lutra', '(Linnaeus, 1758)').

    The name is every leading lower-case word after the genus, so subspecies
    trinomials survive and the authority starts at the first capital or '('.
    """
    words = full.split()
    n = 1
    while (n < len(words) and re.match(r'^[a-zë-]+$', words[n])
           and words[n] not in AUTHOR_PARTICLES):
        n += 1
    return ' '.join(words[:n]), ' '.join(words[n:])


def parse(docx_path):
    root = ET.fromstring(zipfile.ZipFile(docx_path).read('word/document.xml'))
    rows = []
    for tr in root.iter(W + 'tr'):
        cells = [_cell_text(tc) for tc in tr.findall(W + 'tc')]
        if len(cells) != 10 or not cells[0].isdigit():
            continue  # header rows
        no, phylum, _, cls, _, order, _, full, name_ua, cat = cells
        cat = ' '.join(cat.replace('\xa0', ' ').split())
        sci, auth = split_name(full)
        rows.append({
            'no': int(no), 'phylum': phylum, 'class': cls, 'order': order,
            'scientific_name': sci, 'authority': auth, 'name_full': full,
            'name_ua': name_ua, 'category_ua': cat,
            'category': CATEGORY_CODES[cat],
        })
    return rows


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    rows = parse(argv[1])
    with OUT.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f'{len(rows)} taxa -> {OUT}')


if __name__ == '__main__':
    main(sys.argv)
