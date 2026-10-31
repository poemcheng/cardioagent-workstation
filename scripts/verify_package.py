import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
failed=[]
for item in manifest['files']:
    p=ROOT/item['path']
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:failed.append(item['path'])
print(json.dumps({'valid':not failed,'files':len(manifest['files']),'failed':failed},ensure_ascii=False,indent=2))
raise SystemExit(1 if failed else 0)
