"""Read-only source inventory; writes local audit JSON and contact sheets."""
from pathlib import Path
import json
from collections import Counter
from PIL import Image, ImageDraw
import openpyxl

BASE=Path('F:/20_Phototraps_photos/4 - FZS/Ukraine - Carpathians/22_Vyzhnytskyi_NNP_VyzNNP/SMM_Summer_Mammal_Monitoring_VyzNNP_2024/01_Original_Upload')
BOOK=Path('C:/Users/IuriiStrus/Frankfurt Zoological Society/Europe - Ukraine - 060 Biodiversity Monitoring/02_Mammals_CameraTraps/04_Data/BioMonApp_DataUpload/CT_LocationARD_Dataset_2026-10-23_11-27.xlsx')
OUT=Path('logs/vyzh_20261106'); OUT.mkdir(exist_ok=True)
wb=openpyxl.load_workbook(BOOK,read_only=True,data_only=True)
it=iter(wb['SMM_2024'].values); head=next(it)
rows=[dict(zip(head,r)) for r in it]
for r in rows:
    if str(r.get('camera_id')) in ('2204','2205','2207','2208'):
        print(json.dumps({k:v for k,v in r.items() if v is not None},default=str,ensure_ascii=False))
allfiles={}
for cam,folder in [('2204','2204'),('2205','5'),('2207','7'),('2208','2208')]:
    files=[]
    for p in sorted((BASE/folder).rglob('*')):
        if p.suffix.lower() not in ('.jpg','.jpeg'): continue
        with Image.open(p) as im:
            ex=im._getexif() or {}; t=ex.get(36867) or ex.get(306)
        files.append({'path':str(p),'time':t,'size':p.stat().st_size})
    files.sort(key=lambda x:(x['time'] or '',x['path']))
    allfiles[cam]=files
    print(cam,'n=',len(files),'range=',files[0]['time'],files[-1]['time'],'months=',dict(Counter((x['time'] or '')[:7] for x in files)))
    trig=[x for x in files if Path(x['path']).name.upper().startswith('I')]
    chosen=trig[:12]+trig[-12:]
    sheet=Image.new('RGB',(4*480,6*390),'white'); draw=ImageDraw.Draw(sheet)
    for i,x in enumerate(chosen):
        with Image.open(x['path']) as im:
            im.thumbnail((480,360)); sheet.paste(im,((i%4)*480,(i//4)*390))
        draw.text(((i%4)*480,(i//4)*390+360),Path(x['path']).name+' '+str(x['time']),fill='black')
    sheet.save(OUT/(cam+'.jpg'))
(OUT/'files.json').write_text(json.dumps(allfiles,ensure_ascii=False,indent=2),encoding='utf-8')
