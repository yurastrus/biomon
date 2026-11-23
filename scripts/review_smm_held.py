"""Summarize held SMM deployments and save source-photo samples for visual QA."""
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

base = Path('logs/smm_remaining')
def read(name):
    return json.loads((base / (name + '.json')).read_text(encoding='utf-8-sig'))

verified = {r.get('name') for r in map(json.loads, (base/'verified.jsonl').read_text(encoding='utf-8-sig').splitlines()) if r.get('verified')}
locations = {r['name']: r for r in read('db')}
aliases = {'2302 (В8)': 'B 8 (PRK 3)', 'PER1': 'PER 1', 'PER2': 'PER 2', 'DOB 3': 'DOB3'}
for prefix, park in [('18_', 'Karpatskyi NNP'), ('19_', 'Synevyr NNP'), ('21_', 'Verkhovynskyi NNP')]:
    folders = read(prefix+'files')
    held = [r for r in read('targets') if r['study_area_name_EN'] == park and r['deployment_id'] not in verified]
    sheet = Image.new('RGB', (4*410, len(held)*335), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, r in enumerate(held):
        dep = r['deployment_id']; cam = dep.rsplit('_', 1)[-1]; folder = aliases.get(cam, cam)
        start,end = (r[k][:10].replace('-',':') for k in ('start_date','end_date'))
        all_rows = folders[folder]
        valid = sorted((t,p) for p,t in all_rows if t and start <= t[:10] <= end)
        triggers = [(t,p) for t,p in valid if not Path(p).name.upper().startswith('L')]
        groups = {t:p for t,p in triggers}
        times = sorted(groups)
        excluded = Counter(t[:10] if t else 'no EXIF' for _,t in all_rows if not t or not start <= t[:10] <= end)
        dup = Counter((Path(p).name,t) for t,p in valid)
        print(cam, locations[dep]['location_id'], folder, 'all',len(all_rows),'selected',len(valid),'trigger',len(triggers),'distinct trigger stamps',len(groups),'same key extras',sum(n-1 for n in dup.values()), 'excluded',dict(sorted(excluded.items())),flush=True)
        print('  EXIF first/last', valid[0][0],valid[-1][0], 'trigger first/last',times[0],times[-1],flush=True)
        for j,k in enumerate([0,min(1,len(times)-1),len(times)//2,len(times)-1]):
            t = times[k]; path=groups[t]
            with Image.open(path) as image:
                image.thumbnail((410,305)); sheet.paste(image,(410*j,335*i))
            draw.text((410*j+4,335*i+306),f'{cam} {t} {Path(path).name}',fill='black')
    sheet.save(base/(prefix+'held_review.jpg'),quality=92)

samples = [('18_', '1804', '2024:06:06 13:', 4), ('18_', '1813', None, 1),
           ('19_', '1905', '2024:06:09', 4), ('19_', '1911', '2024:07:05', 4),
           ('19_', '1912', '2024:09:15', 3), ('21_', 'DOB1', '2024:07:10', 3),
           ('21_', 'DOB3', '2024:07:10', 3)]
sheet = Image.new('RGB', (4*390,len(samples)*312), 'white')
draw = ImageDraw.Draw(sheet)
for i,(prefix,cam,date,limit) in enumerate(samples):
    rows = read(prefix+'files')[cam]
    rows = [(p,t) for p,t in rows if (not t if date is None else t and t.startswith(date))]
    rows = sorted({t:p for p,t in rows}.items())
    if len(rows)>limit:
        rows = [rows[j*(len(rows)-1)//max(1,limit-1)] for j in range(limit)]
    for j,(t,path) in enumerate(rows):
        try:
            with Image.open(path) as image:
                image.thumbnail((390,288)); sheet.paste(image,(390*j,312*i))
        except OSError:
            print('Unreadable JPEG',cam,Path(path).name,flush=True)
        draw.text((390*j+4,312*i+290),f'{cam} {t} {Path(path).name}',fill='black')
sheet.save(base/'held_boundaries.jpg',quality=94)
