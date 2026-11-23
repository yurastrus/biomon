import json
from collections import Counter
from pathlib import Path

out = Path('logs/smm_remaining')
def read(name):
    return json.loads((out/(name+'.json')).read_text(encoding='utf-8-sig'))

db = read('db')
mapping = {'2302 (В8)':'B 8 (PRK 3)', 'PER1':'PER 1', 'PER2':'PER 2', 'DOB 1':'DOB1', 'DOB 3':'DOB3', 'BUR 3':'BUR3'}
for target in read('targets'):
    dep = target['deployment_id']; cam = dep.rsplit('_',1)[-1]
    prefix = next(p for name,p in [('Karpatskyi','18_'),('Synevyr','19_'),('Verkhovynskyi','21_')] if name in dep)
    if not (out/(prefix+'files.json')).exists():
        continue
    rows = read(prefix+'files').get(mapping.get(cam, cam), [])
    start,end = (target[k][:10].replace('-',':') for k in ('start_date','end_date'))
    valid = sorted((t,p) for p,t in rows if t and start <= t[:10] <= end)
    excluded = Counter(t[:10] if t else 'no EXIF' for _,t in rows if not t or not start <= t[:10] <= end)
    trigger = [(t,p) for t,p in valid if not Path(p).name.upper().startswith('L')]
    loc = [(d['location_id'],d['obs'],d['batches'],d['start_date'],d['end_date']) for d in db if d['name']==dep]
    print(prefix,cam,loc,'n',len(valid),'trigger',len(trigger),'expected',target['n_photos'],
          'range', (valid[0][0],valid[-1][0]) if valid else None,'trigger range', (trigger[0][0],trigger[-1][0]) if trigger else None,
          'excluded',sum(excluded.values()), list(sorted(excluded.items()))[:3])
