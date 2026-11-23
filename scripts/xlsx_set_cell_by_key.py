# SPDX-License-Identifier: AGPL-3.0-only
"""
Point-edit cells of a shared .xlsx WITHOUT re-saving it through openpyxl.

openpyxl drops parts it does not understand (data-validation extensions,
named sheet views, threaded comments, ...), so a load/save round trip would
silently damage the team's ARD workbook. This script rewrites only the one
worksheet XML inside the zip: for each row whose key column matches one of
the given keys, the target column's cell gets a shared-string value. Every
other zip entry is copied byte for byte.

    venv/Scripts/python -m scripts.xlsx_set_cell_by_key FILE --sheet WLCM_2023-24 \
        --key-col deployment_id --col "IS/BD_biomon_dataUpload_status" \
        --value uploaded --keys K1 K2 ...            # dry run: prints old -> new
    ... --apply                                      # writes (backup next to logs/)
The value must already exist in sharedStrings (true for status words).
calcChain is untouched: only constant string cells are changed.
"""
import argparse
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from xml.sax.saxutils import unescape

NS_SHEET = re.compile(r'<sheet\b[^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"')
REL = re.compile(r'<Relationship\b[^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"')
SI = re.compile(r'<si>(.*?)</si>', re.S)
T = re.compile(r'<t[^>]*>(.*?)</t>', re.S)
ROW = re.compile(r'<row\b[^>]*\br="(\d+)"[^>]*>(.*?)</row>', re.S)
CELL = re.compile(r'<c\b([^>]*?)(/>|>(.*?)</c>)', re.S)


def col_letters(ref):
    return re.match(r'[A-Z]+', ref).group(0)


def cell_text(attrs, inner, sst):
    t = re.search(r'\bt="([^"]+)"', attrs)
    v = re.search(r'<v>(.*?)</v>', inner or '', re.S)
    if t and t.group(1) == 's' and v:
        return sst[int(v.group(1))]
    if t and t.group(1) == 'inlineStr':
        return unescape(''.join(T.findall(inner or '')))
    return unescape(v.group(1)) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--sheet', required=True)
    ap.add_argument('--key-col', required=True)
    ap.add_argument('--col', required=True)
    ap.add_argument('--value', required=True)
    ap.add_argument('--keys', nargs='+', required=True)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    z = zipfile.ZipFile(a.file)
    wb = z.read('xl/workbook.xml').decode('utf-8')
    rid = dict(NS_SHEET.findall(wb)).get(a.sheet)
    if not rid:
        sys.exit(f'sheet {a.sheet!r} not found')
    target = dict(REL.findall(z.read('xl/_rels/workbook.xml.rels').decode('utf-8')))[rid]
    part = 'xl/' + target.lstrip('/').replace('xl/', '', 1) if not target.startswith('/xl/') else target[1:]
    sst = [unescape(''.join(T.findall(si))) for si in SI.findall(z.read('xl/sharedStrings.xml').decode('utf-8'))]
    if a.value not in sst:
        sys.exit(f'value {a.value!r} is not in sharedStrings; refusing to add strings')
    vidx = sst.index(a.value)
    xml = z.read(part).decode('utf-8')

    rows = ROW.findall(xml)
    header = {}
    for m in CELL.finditer(rows[0][1]):
        ref = re.search(r'\br="([A-Z]+\d+)"', m.group(1)).group(1)
        header[cell_text(m.group(1), m.group(3), sst)] = col_letters(ref)
    kcol, tcol = header.get(a.key_col), header.get(a.col)
    if not kcol or not tcol:
        sys.exit(f'columns not found: {a.key_col}={kcol} {a.col}={tcol}')

    keys = {k.strip() for k in a.keys}
    found, changes = set(), []

    def fix_row(rm):
        rnum, body = rm.group(1), rm.group(2)
        cells = {col_letters(re.search(r'\br="([A-Z]+\d+)"', m.group(1)).group(1)): m for m in CELL.finditer(body)}
        km = cells.get(kcol)
        key = cell_text(km.group(1), km.group(3), sst) if km else None
        if key is None or key.strip() not in keys:
            return rm.group(0)
        found.add(key.strip())
        tm = cells.get(tcol)
        old = cell_text(tm.group(1), tm.group(3), sst) if tm else None
        changes.append((rnum, key.strip(), old))
        if old == a.value:
            return rm.group(0)
        if tm is None:
            sys.exit(f'row {rnum}: target cell absent; not handled')
        attrs = re.sub(r'\s*\bt="[^"]*"', '', tm.group(1))
        new = f'<c{attrs} t="s"><v>{vidx}</v></c>'
        return rm.group(0).replace(tm.group(0), new, 1)

    new_xml = ROW.sub(fix_row, xml)
    for rnum, key, old in changes:
        print(f'{a.sheet}!{tcol}{rnum}  {key}: {old!r} -> {a.value!r}')
    missing = keys - found
    if missing:
        print('NOT FOUND:', sorted(missing))
    if not a.apply:
        print('DRY RUN. Add --apply.')
        return

    os.makedirs('logs', exist_ok=True)
    backup = os.path.join('logs', f'{os.path.basename(a.file)}.bak_{datetime.now():%Y%m%d_%H%M%S}')
    shutil.copy2(a.file, backup)
    # Build the new zip in memory, then overwrite the file's bytes in place.
    # A rename over the original is refused inside OneDrive/SharePoint-synced
    # folders (WinError 5); writing in place also keeps the file's identity.
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(backup) as src, zipfile.ZipFile(buf, 'w') as dst:
        for info in src.infolist():
            data = new_xml.encode('utf-8') if info.filename == part else src.read(info.filename)
            dst.writestr(info, data, compress_type=info.compress_type)
    with open(a.file, 'r+b') as f:
        f.seek(0)
        f.write(buf.getvalue())
        f.truncate()
    with zipfile.ZipFile(a.file) as chk:
        bad = chk.testzip()
        if bad:
            shutil.copy2(backup, a.file)
            sys.exit(f'written zip is corrupt at {bad}; original restored from backup')
    print('APPLIED. Backup:', backup)


if __name__ == '__main__':
    main()
