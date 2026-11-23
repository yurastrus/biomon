"""Update the shared workbook only for verified deployment IDs."""
import re
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from openpyxl import load_workbook

book = Path('C:/Users/IuriiStrus/Frankfurt Zoological Society/Europe - Ukraine - 060 Biodiversity Monitoring/02_Mammals_CameraTraps/04_Data/BioMonApp_DataUpload/CT_LocationARD_Dataset_2026-10-23_11-27.xlsx')
keys = set(sys.argv[1:]); assert keys
col = 'IS/BD_biomon_dataUpload_status'
def records():
    wb = load_workbook(book,read_only=True,data_only=True)
    rows = iter(wb['SMM_2024'].values); head=next(rows)
    result = [(i,dict(zip(head,r))) for i,r in enumerate(rows,2) if r[head.index('deployment_id')] in keys]
    wb.close()
    assert Counter(r['deployment_id'] for _,r in result) == Counter(keys)
    return result

before = records()
assert all(r[col]=='not uploaded' and r['ISH_qa_dataUpload_status']=='ready for upload' for _,r in before)
args = [sys.executable,'-m','scripts.xlsx_set_cell_by_key',str(book),'--sheet','SMM_2024','--key-col','deployment_id','--col',col,'--value','uploaded','--keys',*sorted(keys)]
dry = subprocess.check_output(args,text=True,encoding='utf-8',env={**__import__('os').environ,'PYTHONUTF8':'1'})
print(dry,flush=True)
assert len(re.findall(r"^SMM_2024!I\d+ .*'not uploaded' -> 'uploaded'$",dry,re.M)) == len(keys)
applied = subprocess.check_output([*args,'--apply'],text=True,encoding='utf-8',env={**__import__('os').environ,'PYTHONUTF8':'1'})
print(applied,flush=True)
backup = Path(applied.split('APPLIED. Backup: ')[1].strip())
assert all(r[col]=='uploaded' for _,r in records())
with zipfile.ZipFile(book) as now, zipfile.ZipFile(backup) as old:
    assert set(now.namelist()) == set(old.namelist())
    part, = [name for name in old.namelist() if old.read(name) != now.read(name)]
    assert part == 'xl/worksheets/sheet10.xml'
    refs = {f'I{i}'.encode() for i,_ in before}
    pattern = rb'(<c\b[^>]*\br="(I\d+)"[^>]*><v>)\d+(</v></c>)'
    def mask(data):
        return re.sub(pattern,lambda m: m[1]+m[3] if m[2] in refs else m[0],data)
    assert mask(old.read(part)) == mask(now.read(part))
print(f'Verified exactly {len(keys)} status cells')
