import hashlib
import json
import tarfile
import sys
from pathlib import Path

out = Path('logs/vyzh_20261106')
files = json.loads((out / 'files.json').read_text(encoding='utf-8'))
targets = {'2205': (3236, '2024:06:25', '2024:09:25'),
           '2207': (3233, '2024:06:25', '2024:10:04'),
           '2208': (3234, '2024:06:28', '2024:10:28')}
extra = sys.argv[1:] == ['2204']
if extra:
    targets = {'2204': (3235, '2024:07:24', '2024:10:27')}
with tarfile.open(out / ('2204.tar' if extra else 'pilot.tar'), 'w') as archive:
    for cam, (loc, start, end) in targets.items():
        rows = [r for r in files[cam] if start <= r['time'][:10] <= end]
        seen = {}
        for i, row in enumerate(rows):
            path = Path(row['path'])
            key = (path.name, row['time'])
            digest = hashlib.sha256(path.read_bytes()).digest()
            if key in seen:
                if digest != seen[key]:
                    raise RuntimeError(f'Different files with same name and EXIF: {cam} {key}')
                continue
            seen[key] = digest
            archive.add(path, arcname=f'{loc}/{i:05d}/{path.name}', recursive=False)
        print(cam, loc, len(seen), 'outside dates', len(files[cam])-len(rows),
              'identical copies', len(rows)-len(seen), rows[0]['time'], rows[-1]['time'])
    archive.add('scripts/server_upload_folder.py', arcname='upload.py', recursive=False)
