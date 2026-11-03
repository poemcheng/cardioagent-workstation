"""Verify the release manifest without following paths outside its root."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    root=Path(root).resolve()
    manifest=json.loads((root/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
    failed=[];seen=set();items=manifest['files']
    if not isinstance(items,list) or not items:raise ValueError('Package manifest has no files.')
    for item in items:
        name=item.get('path','');relative=Path(name)
        if (not name or relative.is_absolute() or '..' in relative.parts or '\\' in name
                or name in seen or not re.fullmatch('[a-f0-9]{64}',item.get('sha256',''))):
            failed.append(name or '<invalid>');continue
        seen.add(name);path=(root/relative).resolve()
        if (not path.is_relative_to(root) or not path.is_file()
                or ('bytes' in item and path.stat().st_size!=item['bytes'])
                or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']):failed.append(name)
    return {'valid':not failed,'files':len(items),'failed':failed}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args(argv)
    try:result=verify(args.root)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result={'valid':False,'files':0,'failed':[],'error':str(exc),'note':'A source checkout is not a packaged release. Download the Windows release or run the release builder first.'}
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['valid'] else 1


if __name__=='__main__':raise SystemExit(main())
