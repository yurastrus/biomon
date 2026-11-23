import json
import sys
from collections import Counter
from pathlib import Path
from PIL import Image
from openpyxl import load_workbook

root = Path('F:/20_Phototraps_photos/4 - FZS/Ukraine - Carpathians')
book = Path('C:/Users/IuriiStrus/Frankfurt Zoological Society/Europe - Ukraine - 060 Biodiversity Monitoring/02_Mammals_CameraTraps/04_Data/BioMonApp_DataUpload/CT_LocationARD_Dataset_2026-10-23_11-27.xlsx')
rows = iter(load_workbook(book, read_only=True, data_only=True)['SMM_2024'].values)
head = next(rows)
targets = [dict(zip(head, row)) for row in rows]
targets = [r for r in targets if r.get('ISH_qa_dataUpload_status') == 'ready for upload' and r.get('IS/BD_biomon_dataUpload_status') == 'not uploaded']
out = Path('logs/smm_remaining'); out.mkdir(exist_ok=True)
(out/'targets.json').write_text(json.dumps(targets, default=str, ensure_ascii=False), encoding='utf-8')
for prefix in (sys.argv[1:] or ('18_', '19_', '21_')):
    park = next(root.glob(prefix+'*'))
    campaign = next(park.glob('SMM*2024'))
    base = campaign / ('02_Collections' if prefix == '18_' else '01_Original_Upload')
    sets = {}
    wanted = {r['deployment_id'].rsplit('_',1)[-1] for r in targets if 'Synevyr NNP_' in r['deployment_id']}
    for path in base.rglob('*'):
        if path.suffix.lower() not in ('.jpg', '.jpeg') or not path.is_file():
            continue
        rel = path.relative_to(base)
        parts = rel.parts
        key = parts[1] if prefix == '21_' and len(parts)>2 else parts[0]
        if prefix == '19_' and key not in wanted:
            continue
        try:
            with Image.open(path) as im:
                ex = im._getexif() or {}; ts = ex.get(36867) or ex.get(306)
        except (OSError, ValueError):
            ts = None
        sets.setdefault(key, []).append([str(path), ts])
    (out/(prefix+'files.json')).write_text(json.dumps(sets,ensure_ascii=False), encoding='utf-8')
    for k, v in sorted(sets.items()):
        times = sorted(t for _,t in v if t)
        print(prefix,k,len(v),len(v)-len(times),times[:1],times[-1:],dict(Counter(t[:7] for t in times)),flush=True)
