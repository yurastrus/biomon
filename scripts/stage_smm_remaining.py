import hashlib
import io
import json
import sys
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from compare_smm_remaining import read, out, mapping

requested = set(sys.argv[2:])
held = sys.argv[1] == 'held_high'
offsets = {'1903': 43200, '1911': 43200} if held else {}
starts = {'1804': '2024:06:06 13:23:00', '1813': '2024:06:17 13:32:00'} if held else {}
ends = {'1912': '2024:09:15'} if held else {}
manifest = []
with tarfile.open(out/(sys.argv[1]+'.tar'),'w') as archive:
    for target in read('targets'):
        dep = target['deployment_id']; cam = dep.rsplit('_',1)[-1]
        if cam not in requested:
            continue
        prefix = next(p for name,p in [('Karpatskyi','18_'),('Synevyr','19_'),('Verkhovynskyi','21_')] if name in dep)
        loc, = [r['location_id'] for r in read('db') if r['name']==dep and r['location_id'] and not r['obs'] and not r['batches']]
        start,end = (target[k][:10].replace('-',':') for k in ('start_date','end_date'))
        offset = offsets.get(cam,0)
        start = starts.get(cam,start)
        end = ends.get(cam,end)
        seen = {}; hashes = set(); keys = []
        rows = read(prefix+'files')[mapping.get(cam,cam)]
        excluded = 0
        for i,(p,t) in enumerate(sorted(rows)):
            if not t or t < start or t[:10] > end:
                excluded += 1
                continue
            path = Path(p); key = (path.name,t); data = path.read_bytes(); sha = hashlib.sha256(data).digest()
            if key in seen and sha != seen[key]:
                raise ValueError((cam,key,'different photos share the same key'))
            if sha in hashes:
                continue
            seen[key]=sha; hashes.add(sha)
            corrected = datetime.strptime(t,'%Y:%m:%d %H:%M:%S') + timedelta(seconds=offset)
            keys.append({'name':path.name,'ts':corrected.isoformat()})
            entry = tarfile.TarInfo(f'{loc}/{i:05d}/{path.name}'); entry.size=len(data)
            archive.addfile(entry,io.BytesIO(data))
        assert keys
        manifest.append({'loc':loc,'dep':dep,'keys':keys,'offset':offset,'excluded':excluded})
        print('STAGED',cam,loc,len(keys),'offset',offset,'excluded',excluded,flush=True)
    assert {i['dep'].rsplit('_',1)[-1] for i in manifest} == requested
    data=json.dumps(manifest,ensure_ascii=False).encode('utf-8')
    entry=tarfile.TarInfo('manifest.json'); entry.size=len(data); archive.addfile(entry,io.BytesIO(data))
    archive.add('scripts/server_upload_folder.py','upload.py')
    archive.add('scripts/run_smm_staged.py','run.py')
